from inaugurator import partitiontable
from inaugurator import targetdevice
from inaugurator import mount
from inaugurator import sh
from inaugurator import network
from inaugurator import loadkernel
from inaugurator import fstab
from inaugurator import passwd
from inaugurator import osmose
from inaugurator import osmosiscleanup
from inaugurator import talktoserver
from inaugurator import grub
from inaugurator import diskonkey
from inaugurator import cdrom
from inaugurator import udev
from inaugurator import download
from inaugurator import etclabelfile
from inaugurator import lvmetad
from inaugurator import verify
from inaugurator import debugthread
import os
import re
import time
import logging
import threading


class Ceremony:
    def __init__(self, args):
        """
        args is a 'namespace' - an object, or maybe a bunch. The following members are required:
        inauguratorClearDisk - True will cause the disk to be erase even if partition layout is ok
        inauguratorSource - 'network', 'DOK' (Disk On Key), 'CDROM' or 'local' - select from where the label
                            should be osmosed. 'local' means the label is already in the local object
                            store, and is used in upgrades.
        inauguratorServerAMQPURL - the rabbitmq AMQP url to report status to. Can be 'None'. If used,
                                   the label itself is expected to come from a rabbitmq message.
        inauguratorMyIDForServer - the unique ID for this station, used for status reporting.
        inauguratorNetworkLabel - the label to use, in 'network' mode, if inauguratorServerAMQPURL was
                                  not specified
        inauguratorOsmosisObjectStores - the object store chain used when invoking osmosis (see osmosis
                                         documentation
        inauguratorUseNICWithMAC - use this specific NIC, with this specific MAC address
        inauguratorIPAddress - the IP address to configure to that NIC
        inauguratorNetmask
        inauguratorGateway
        inauguratorChangeRootPassword - change the password in /etc/shadow to this
        inauguratorWithLocalObjectStore - use /var/lib/osmosis local object store as first tier in chain.
        inauguratorPassthrough - pass parameters to the kexeced kernel. Reminder: kexeced kernel are
                                 more vunerable to crashing, using this as the only channel of communication
                                 is risky
        inauguratorDownload - http get this file into a specific location, right before kexecing.
        inauguratorIgnoreDirs - ignore the following locations on disk, in the osmosis process. This is
                                usedful for upgrades - to keep the current configuration somewhere.
        inauguratorTargetDeviceCandidate - a list of devices (['/dev/vda', '/dev/sda']) to use as the
                                           inauguration target
        """
        self._args = args
        self._talkToServer = None
        self._assertArgsSane()
        self._debugPort = None
        self._isExpectingReboot = False
        self._grubConfig = None
        self._localObjectStore = None

    def ceremony(self):
        before = time.time()
        self._makeSureDiskIsMountable()
        if self._args.inauguratorDisableNCQ:
            self._disableNCQ()
        else:
            print 'Skipping the disabling of NCQ.'
        with self._mountOp.mountRoot() as destination, self._mountOp.mountOsmosisCache() as osmosisCache:
            self._localObjectStore = osmosisCache
            self._etcLabelFile = etclabelfile.EtcLabelFile(destination)
            self._doOsmosisFromSource(destination)
            logging.info("Osmosis complete")
            self._createBootAndInstallGrub(destination)
            logging.info("Boot sync complete")
            self._configureETC(destination)
            self._loadKernelForKexecing(destination)
            logging.info("kernel loaded")
            self._additionalDownload(destination)
        self._sync()
        if self._args.inauguratorVerify:
            self._verify()
            self._sync()
        after = time.time()
        if self._talkToServer is not None:
            self._talkToServer.done()
        logging.info("Inaugurator took: %(interval).2fs. KEXECing", dict(interval=after - before))
        self._loadKernel.execute()

    def _assertArgsSane(self):
        logging.info("Command line arguments: %(args)s", dict(args=self._args))
        if self._args.inauguratorSource == "network":
            assert (
                (self._args.inauguratorServerAMQPURL or self._args.inauguratorNetworkLabel) and
                self._args.inauguratorOsmosisObjectStores and
                self._args.inauguratorUseNICWithMAC and self._args.inauguratorIPAddress and
                self._args.inauguratorNetmask and self._args.inauguratorGateway), \
                "If inauguratorSource is 'network', all network command line paramaters must be specified"
            if self._args.inauguratorServerAMQPURL:
                assert self._args.inauguratorMyIDForServer, \
                    'If communicating with server, must specifiy --inauguratorMyIDForServer'
        elif self._args.inauguratorSource in ["DOK", "local", "CDROM"]:
            pass
        else:
            assert False, "Unknown source for inaugurator: %s" % self._args.inauguratorSource

    def _createPartitionTable(self):
        lvmetad.Lvmetad()
        partitionTable = partitiontable.PartitionTable(self._targetDevice)
        if self._args.inauguratorClearDisk:
            partitionTable.clear()
        partitionTable.verify()

    def _configureETC(self, destination):
        self._etcLabelFile.write(self._label)
        fstab.createFSTab(
            rootPath=destination, root=self._mountOp.rootPartition(),
            boot=self._mountOp.bootPartition(), swap=self._mountOp.swapPartition())
        logging.info("/etc/fstab created")
        if self._args.inauguratorChangeRootPassword:
            passwd.setRootPassword(destination, self._args.inauguratorChangeRootPassword)
            logging.info("Changed root password")

    def _getSerialDevice(self):
        with open("/proc/cmdline", "r") as cmdLineFile:
            cmdLine = cmdLineFile.read()
        pattern = re.compile("(^| )+console=(\S+)( |$)+")
        match = pattern.search(cmdLine)
        if match is None:
            return None
        return match.groups()[1]

    def _createBootAndInstallGrub(self, destination):
        with self._mountOp.mountBoot() as bootDestination:
            sh.run("rsync -rlpgDS --delete-before %s/boot/ %s/" % (destination, bootDestination))
        with self._mountOp.mountBootInsideRoot():
            serialDevice = self._getSerialDevice()
            if serialDevice is None:
                logging.warn("a 'console' argument was not given. Cannot tell which serial device to "
                             "redirect the console output to (default values in the label will be used).")
            else:
                logging.info("Overriding GRUB2 user settings to set serial device to '%(device)s'...",
                             dict(device=serialDevice))
                grub.setSerialDevice(serialDevice, destination)
            logging.info("Installing GRUB2...")
            grub.install(self._targetDevice, destination)
            logging.info("Reading newly generated GRUB2 configuration file for later use...")
            grubConfigFilename = os.path.join(destination, "boot", "grub2", "grub.cfg")
            with open(grubConfigFilename, "r") as grubConfigFile:
                self._grubConfig = grubConfigFile.read()

    def _osmosFromNetwork(self, destination):
        network.Network(
            macAddress=self._args.inauguratorUseNICWithMAC, ipAddress=self._args.inauguratorIPAddress,
            netmask=self._args.inauguratorNetmask, gateway=self._args.inauguratorGateway)
        self._debugPort = debugthread.DebugThread()
        if self._args.inauguratorServerAMQPURL:
            self._talkToServer = talktoserver.TalkToServer(
                amqpURL=self._args.inauguratorServerAMQPURL, myID=self._args.inauguratorMyIDForServer)
            self._talkToServer.checkIn()
        try:
            osmos = osmose.Osmose(
                destination=destination,
                objectStores=self._args.inauguratorOsmosisObjectStores,
                withLocalObjectStore=self._args.inauguratorWithLocalObjectStore,
                localObjectStore=self._localObjectStore,
                ignoreDirs=self._args.inauguratorIgnoreDirs,
                talkToServer=self._talkToServer)
            if self._args.inauguratorServerAMQPURL:
                self._label = self._talkToServer.label()
            else:
                self._label = self._args.inauguratorNetworkLabel
            osmos.tellLabel(self._label)
            osmos.wait()
        except Exception as e:
            if self._debugPort is not None and self._debugPort.wasRebootCalled():
                logging.info("Waiting to be reboot (from outside)...")
                blockForever = threading.Event()
                blockForever.wait()
            else:
                try:
                    self._talkToServer.failed(message=str(e))
                except:
                    pass
            raise e

    def _osmosFromDOK(self, destination):
        dok = diskonkey.DiskOnKey()
        with dok.mount() as source:
            osmos = osmose.Osmose(
                destination, objectStores=source + "/osmosisobjectstore",
                withLocalObjectStore=self._args.inauguratorWithLocalObjectStore,
                localObjectStore=self._localObjectStore,
                ignoreDirs=self._args.inauguratorIgnoreDirs,
                talkToServer=self._talkToServer)
            with open("%s/inaugurate_label.txt" % source) as f:
                self._label = f.read().strip()
            osmos.tellLabel(self._label)  # This must stay under the dok mount 'with' statement
            osmos.wait()

    def _osmosFromCDROM(self, destination):
        cdromInstance = cdrom.Cdrom()
        with cdromInstance.mount() as source:
            osmos = osmose.Osmose(
                destination, objectStores=source + "/osmosisobjectstore",
                withLocalObjectStore=self._args.inauguratorWithLocalObjectStore,
                localObjectStore=self._localObjectStore,
                ignoreDirs=self._args.inauguratorIgnoreDirs,
                talkToServer=self._talkToServer)
            with open("%s/inaugurate_label.txt" % source) as f:
                self._label = f.read().strip()
            osmos.tellLabel(self._label)  # This must stay under the mount 'with' statement
            osmos.wait()

    def _osmosFromLocalObjectStore(self, destination):
        osmos = osmose.Osmose(
            destination, objectStores=None,
            withLocalObjectStore=self._args.inauguratorWithLocalObjectStore,
            localObjectStore=self._localObjectStore,
            ignoreDirs=self._args.inauguratorIgnoreDirs,
            talkToServer=self._talkToServer)
        self._label = self._args.inauguratorNetworkLabel
        osmos.tellLabel(self._label)
        osmos.wait()

    def _sync(self):
        logging.info("sync...")
        sh.run(["busybox", "sync"])
        logging.info("sync done")

    def _additionalDownload(self, destination):
        if self._args.inauguratorDownload:
            downloadInstance = download.Download(self._args.inauguratorDownload)
            downloadInstance.download(destination)

    def _makeSureDiskIsMountable(self):
        udev.loadAllDrivers()
        self._targetDevice = targetdevice.TargetDevice.device(self._args.inauguratorTargetDeviceCandidate)
        self._createPartitionTable()
        logging.info("Partitions created")
        self._mountOp = mount.Mount(self._targetDevice)

    def _loadKernelForKexecing(self, destination):
        self._loadKernel = loadkernel.LoadKernel()
        self._loadKernel.fromBootPartitionGrubConfig(
            grubConfig=self._grubConfig,
            bootPath=os.path.join(destination, "boot"), rootPartition=self._mountOp.rootPartition(),
            append=self._args.inauguratorPassthrough)

    def _doOsmosisFromSource(self, destination):
        osmosiscleanup.OsmosisCleanup(destination, objectStorePath=self._localObjectStore)
        if self._args.inauguratorSource == 'network':
            self._osmosFromNetwork(destination)
        elif self._args.inauguratorSource == 'DOK':
            self._osmosFromDOK(destination)
        elif self._args.inauguratorSource == 'CDROM':
            self._osmosFromCDROM(destination)
        elif self._args.inauguratorSource == 'local':
            self._osmosFromLocalObjectStore(destination)
        else:
            assert False, "Unknown source %s" % self._args.inauguratorSource

    def _verify(self):
        verify.Verify.dropCaches()
        with self._mountOp.mountRoot() as destination:
            verify.Verify(destination, self._label, self._talkToServer).go()

    def _getSSDDeviceNames(self):
        blockDevices = os.listdir('/sys/block')
        storageDevices = [dev for dev in blockDevices if dev.startswith('sd')]
        ssdDevices = []
        for device in storageDevices:
            isRotationalPathComponents = ['sys', 'block', device, 'queue', 'rotational']
            isRotationalPath = os.path.join(*isRotationalPathComponents)
            with open(isRotationalPath, 'rb') as f:
                isRotational = f.read()
            isRotational = bool(int(isRotational.strip()))
            if not isRotational:
                ssdDevices.append(device)
        return ssdDevices

    def _disableNCQ(self):
        devices = self._getSSDDeviceNames()
        if not devices:
            print 'Did not find any non-rotational storage devices on which to disable NCQ.'
            return
        print 'Disabling NCQ for the following SSD devices: {}...'.format(devices)
        for device in devices:
            try:
                queueDepthPath = '/sys/block/{}/device/queue_depth'.format(device)
                print sh.run('busybox echo 1 > {}'.format(queueDepthPath))
                print sh.run('busybox echo "{} is now:" '.format(queueDepthPath))
                print sh.run('busybox cat {}'.format(queueDepthPath))
            except Exception, ex:
                print ex.message

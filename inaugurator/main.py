from inaugurator import checkinwithserver
from inaugurator import udev
from inaugurator import network
from inaugurator import sh
import argparse
import traceback
import pdb
import os
import time
import logging
import sys
from inaugurator import lvmetad
from inaugurator import targetdevice


logging.basicConfig(level=logging.DEBUG, stream=sys.stdout)


def main(args):
    before = time.time()
    print 'Starting udev...'
    udev.loadAllDrivers()
    print 'Searching target device...'
    targetDevice = targetdevice.TargetDevice.device()
    print 'Starting lvmetad...'
    lvmetad.Lvmetad()
    print 'initializing network...'
    network.Network(macAddress=args.inauguratorUseNICWithMAC, ipAddress=args.inauguratorIPAddress,
                    netmask=args.inauguratorNetmask, gateway=args.inauguratorGateway)
    print 'checking in...'
    checkIn = checkinwithserver.CheckInWithServer(hostname=args.inauguratorServerHostname)

    import socket
    import sys
    import subprocess
    HOST = ''   # Symbolic name, meaning all available interfaces
    PORT = 8888  # Arbitrary non-privileged port
    s = socket.socket()
    # s.setsockopt(socket.SOL_TCP, socket.TCP_NODELAY, 1)
    print 'Socket created'
    try:
        s.bind((HOST, PORT))
    except socket.error as msg:
        print 'Bind failed. Error Code : ' + str(msg[0]) + ' Message ' + msg[1]
        sys.exit()
    print 'Socket bind complete'
    s.listen(10)
    while 1:
        print 'Socket now listening'
        conn, addr = s.accept()
        time.sleep(1)
        print 'Connected with ' + addr[0] + ':' + str(addr[1])
        while True:
            time.sleep(1)
            try:
                print 'waiting for a command...'
                cmd = conn.recv(1000)
            except:
                traceback.print_exc(file=sys.stdout)
                break
            try:
                print 'command: {}'.format(cmd)
                print sh.run(cmd)
            except:
                traceback.print_exc(file=sys.stdout)
        s.close()
    print 'sleeping...'
    time.sleep(10)
    with open(r'sshd_config', 'wb') as f:
        f.write('# bla')
    import subprocess
    print subprocess.check_output('/usr/sbin/sshd-keygen')
    time.sleep(2)
    print subprocess.check_output('/usr/sbin/sshd -f sshd_config')
    time.sleep(4)


parser = argparse.ArgumentParser(add_help=False)
parser.add_argument("--inauguratorClearDisk", action="store_true")
parser.add_argument("--inauguratorSource", required=True)
parser.add_argument("--inauguratorServerHostname")
parser.add_argument("--inauguratorNetworkLabel")
parser.add_argument("--inauguratorOsmosisObjectStores")
parser.add_argument("--inauguratorUseNICWithMAC")
parser.add_argument("--inauguratorIPAddress")
parser.add_argument("--inauguratorNetmask")
parser.add_argument("--inauguratorGateway")
parser.add_argument("--inauguratorChangeRootPassword")
parser.add_argument("--inauguratorWithLocalObjectStore", action="store_true")
parser.add_argument("--inauguratorPassthrough", default="")
parser.add_argument("--inauguratorDownload", nargs='+', default=[])
parser.add_argument("--inauguratorIgnoreDirs", nargs='+', default=[])

try:
    cmdLine = open("/proc/cmdline").read().strip()
    args = parser.parse_known_args(cmdLine.split(' '))[0]
    print "Command line arguments:", args
    if args.inauguratorSource == "network":
        assert (
            (args.inauguratorServerHostname or args.inauguratorNetworkLabel) and
            args.inauguratorOsmosisObjectStores and
            args.inauguratorUseNICWithMAC and args.inauguratorIPAddress and
            args.inauguratorNetmask and args.inauguratorGateway), \
            "If inauguratorSource is 'network', all network command line paramaters must be specified"
    elif args.inauguratorSource == "DOK":
        pass
    elif args.inauguratorSource == "local":
        pass
    else:
        assert False, "Unknown source for inaugurator: %s" % args.inauguratorSource
    main(args)
except Exception as e:
    print "Inaugurator raised exception: "
    traceback.print_exc(e)
finally:
    pdb.set_trace()

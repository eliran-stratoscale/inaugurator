IMAGES_SOURCE=__undefined
IMAGES = $(IMAGES_SOURCE)/inaugurator.thin.initrd.img $(IMAGES_SOURCE)/inaugurator.fat.initrd.img $(IMAGES_SOURCE)/inaugurator.vmlinuz

all: build unittest check_convention

install: $(IMAGES)
	$(MAKE) install_nodeps

clean:
	rm -fr build docker-build remote dist inaugurator.egg-info

check_convention:
	pep8 inaugurator --max-line-length=109 --exclude=samplegrubconfigs.py
	sh/check_spelling.sh

UNITTESTS=$(shell find inaugurator -name 'test*.py' | sed 's@/@.@g' | sed 's/\(.*\)\.py/\1/' | sort)
unittest:
	PYTHONPATH=. python -m unittest $(UNITTESTS)

integration_test:
	@echo "Note: For specific tests, run with TESTS=(Space seperated test names)."
	@PYTHONPATH=inaugurator python inaugurator/tests/integration_test.py $(TESTS)

include Makefile.build

uninstall:
	-sudo mkdir /usr/share/inaugurator
	-yes | sudo pip uninstall inaugurator

remote/%:
	sudo solvent bring --repositoryBasename=inaugurator --product build --destination=remote

__undefined/%:
	$(error Please specify the environment variable IMAGES_SOURCE, to indicate how to obtain inaugurator images, as either 'build' (build images locally) or 'remote' (bring images from solvent object store))

.PHONY: submit
submitclean:
	SOLVENT_CLEAN=1 solvent submitbuild
	SOLVENT_CLEAN=1 solvent approve

install_nodeps:
	-sudo mkdir /usr/share/inaugurator
	-yes | sudo pip uninstall inaugurator
	python setup.py bdist
	sudo python setup.py install
	sudo cp $(IMAGES) /usr/share/inaugurator
	sudo chmod 644 /usr/share/inaugurator/*

prepareForCleanBuild:
	sudo pip install pika

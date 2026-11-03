#!/bin/bash

VBoxManage createvm --name $1 --ostype Ubuntu_64 --register
VBoxManage modifyvm $1 --ioapic on
VBoxManage modifyvm $1 --memory $2 --vram 16       
VBoxManage modifyvm $1 --nic1 bridged --bridgeadapter1 enp7s0
VBoxManage createhd --filename "/home/cmr/VirtualBox VMs/$1/$1.vdi" --size $3 --format VDI --variant Standard
VBoxManage storagectl $1 --name "SATA Controller" --add sata --controller IntelAhci
VBoxManage storageattach $1 --storagectl "SATA Controller" --port 0 --device 0 --type hdd --medium "/home/cmr/VirtualBox VMs/$1/$1.vdi"
VBoxManage storagectl $1 --name "IDE Controller" --add ide --controller PIIX4
VBoxManage storageattach $1 --storagectl "IDE Controller" --port 1 --device 0 --type dvddrive --medium /home/cmr/jeeyessos/iso/ubuntu-18.04.5-live-server-amd64.iso
VBoxManage modifyvm $1 --boot1 dvd --boot2 disk --boot3 none --boot4 none

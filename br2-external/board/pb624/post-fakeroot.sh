#!/bin/sh
# Runs on the copy of the target the rootfs image is made from, after the
# device/permission tables: /etc/{shadow,passwd} -> /data/config/ (see
# post-build.sh and S00data)
set -e
ln -snf /data/config/shadow "$1/etc/shadow"
ln -snf /data/config/passwd "$1/etc/passwd"

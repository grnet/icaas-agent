#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# Copyright (C) 2015 GRNET S.A.
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

"""This module is the entrance point for the program that monitors the progress
made and uploads the result to a pithos deployment.
"""
from __future__ import print_function

import argparse
import sys
import os
import ConfigParser
import signal
import daemon
import time
import subprocess
import syslog
import tempfile
import requests

from kamaki.clients import ClientError
from kamaki.clients.utils import https
from kamaki.clients.astakos import AstakosClient, AstakosClientError
from kamaki.clients.pithos import PithosClient

from icaas_agent import __version__ as version
from icaas_agent.scripts import get_script
from icaas_agent.report import Report

CERTS = '/etc/ssl/certs/ca-certificates.crt'
NAME = os.path.basename(sys.argv[0])
PID = '/var/run/%s.pid' % NAME


def error(msg):
    """Print an error message"""
    syslog.syslog(syslog.LOG_ERR, msg)
    sys.stderr.write("Error: %s\n" % msg)


def read_manifest(manifest):
    """Read the manifest file"""

    config = ConfigParser.ConfigParser()

    syslog.syslog(syslog.LOG_INFO, "Reading manifest file: `%s'" % manifest)
    if len(config.read(manifest)) == 0:
        error("Manifest file: `%s' is not parsable" % manifest)
        sys.exit(2)

    manifest = {}
    manifest['service'] = {}
    if 'service' in config.sections():
        for key, value in config.items('service'):
            manifest['service'][key] = value

    manifest['image'] = {}
    if 'image' in config.sections():
        for key, value in config.items('image'):
            manifest['image'][key] = value

    manifest['synnefo'] = {}
    if 'synnefo' in config.sections():
        for key, value in config.items('synnefo'):
            manifest['synnefo'][key] = value

    manifest['log'] = {}
    if 'log' in config.sections():
        for key, value in config.items('log'):
            manifest['log'][key] = value

    if 'manifest' in config.sections():
        manifest['manifest'] = {}
        for key, value in config.items('manifest'):
            manifest['manifest'][key] = value

        if 'url' in manifest['manifest']:
            url = manifest['manifest']['url']
            r = requests.get(url)
            if r.status_code != requests.codes.ok:
                error("Fetching manifest from %s failed: (%d) %s" %
                      (url, r.status_code, r.text))
                sys.exit(3)
            try:
                fetched = r.json()
                manifest.update(fetched['manifest'])
            except KeyError:
                error("Invalid manifest fetching response: %s", r.text)
                sys.exit(4)

    return manifest


def do_main_loop(interval, client, name):
    """Main loop of the monitord service"""

    try:
        client.create_container(client.container)
    except ClientError as err:
        if err.status != 202:  # Ignore container already exists errors
            raise
        else:
            syslog.syslog(syslog.LOG_WARNING,
                          "Container: `%s' already exists." % client.container)

    monitor = tempfile.NamedTemporaryFile(prefix='icaas-log-')
    icaas = subprocess.Popen(['/bin/bash', get_script('create_image')],
                             stdout=monitor, stderr=monitor)

    cnt = 0
    while True:
        cnt += 1
        with open(monitor.name, "r") as m:
            client.upload_object(name, m, content_type="text/plain")
        syslog.syslog(syslog.LOG_NOTICE,
                      'uploaded monitoring file for the %d time' % cnt)
        if icaas.poll() is not None:
            # The script has finished. Upload the log file for one last time to
            # make sure all the script output is upstream
            cnt += 1
            with open(monitor.name, "r") as m:
                client.upload_object(name, m, content_type="text/plain")
            syslog.syslog(syslog.LOG_NOTICE,
                          'uploaded monitoring file for the %d time' % cnt)

            if icaas.returncode == 0:
                return True
            else:  # error
                with open(monitor.name, "r") as m:
                    sys.stderr.write("".join(m.readlines()))
                return False

        time.sleep(interval)


def get_args():
    """Get input arguments"""

    parser = argparse.ArgumentParser(description="Monitoring daemon for icaas",
                                     version=version)
    parser.add_argument("-d", "--daemonize", dest="daemonize", default=False,
                        action="store_true",
                        help="detach the process from the shell")
    parser.add_argument("-m", "--manifest", dest="manifest",
                        metavar="MANIFEST", default="/etc/icaas/manifest.cfg",
                        help="specifies the name of the manifest file. The "
                        "default is %(default)s.")
    parser.add_argument("-i", "--interval", dest="interval", default=5,
                        type=int, metavar="INTERVAL",
                        help="upload the file every %(metavar)s seconds")
    args = parser.parse_args()

    if os.path.isfile(PID):
        error("Prοgram is already running. If this is not the case, please "
              "delete %s" % PID)
        sys.exit(2)

    if not os.path.isfile(args.manifest):
        parser.error("Manifest file: `%s' not found. Use -m to specify a "
                     "different path" % args.manifest)

    if args.interval < 1:
        parser.error("Interval must be at least 1")

    return args


def main():
    """Entry point for icaas-monitord"""

    args = get_args()

    manifest = read_manifest(args.manifest)

    missing = '"%s" is missing from the "%s" section of the manifest\n'

    for item in 'status', 'token':
        if item not in manifest['service']:
            print(missing % (item, 'service'), file=sys.stderr)
            sys.exit(3)

    if 'insecure' in manifest['service'] and \
            manifest['service']['insecure'].lower() == 'true':
        verify = False
    else:
        verify = True

    report = Report(manifest['service']['status'],
                    manifest['service']['token'],
                    verify=verify, log=sys.stderr)

    report.progress("Booted!")

    def missing_key(key, section):
        """missing key message"""
        return "`%s' is missing from the `%s' section of the manifest" % \
            (key, section)

    # Validate the manifest
    for key in 'url', 'token':
        if key not in manifest['synnefo']:
            report.error(missing_key(key, 'synnefo'))
            sys.exit(3)

    for key in 'src', 'name', 'container', 'object':
        if key not in manifest['image']:
            report.error(missing_key(key, 'image'))
            sys.exit(3)

    for key in 'container', 'object':
        if key not in manifest['log']:
            report.error(missing_key(key, 'log'))
            sys.exit(3)

    # Use the systems certificates
    https.patch_with_certs(CERTS)

    account = AstakosClient(manifest['synnefo']['url'],
                            manifest['synnefo']['token'])
    try:
        account.authenticate()
    except AstakosClientError as err:
        report.error("Astakos: %s" % err)
        sys.exit(3)

    user = account.user_info['id'] if 'account' not in manifest['log'] else \
        manifest['log']['account']

    pithos = PithosClient(
        account.get_service_endpoints('object-store')['publicURL'],
        account.token, user, manifest['log']['container'])

    if args.daemonize:
        daemon_context = daemon.DaemonContext(stdin=sys.stdin,
                                              stdout=sys.stdout,
                                              stderr=sys.stderr)
        daemon_context.open()

    with open(PID, 'w') as pid:
        pid.write("%d\n" % os.getpid())

    try:
        # Export manifest to environment variables
        for section in manifest:
            for key, value in manifest[section].items():
                name = "ICAAS_%s_%s" % (section.upper(), key.upper())
                os.environ[name] = value

        # Use SIGHUP to unblock from the sleep if necessary
        signal.signal(signal.SIGHUP, lambda x, y: None)

        if 'ICAAS_MONITOR_SIGSTOP' in os.environ:
            # Tell service supervisor that we are ready.
            syslog.syslog(
                syslog.LOG_NOTICE, "Stopping with SIGSTOP as the "
                "environment variable ICAAS_MONITOR_SIGSTOP is defined")
            os.kill(os.getpid(), signal.SIGSTOP)
            del os.environ['ICAAS_MONITOR_SIGSTOP']

        def terminate(signum, frame):
            """Shut down gracefully on a SIGTERM or a SIGINT signal"""
            report.error("Image creation failed. Stopped before completing")
            name = 'SIGINT' if signum == signal.SIGINT else 'SIGTERM'
            syslog.syslog(syslog.LOG_NOTICE,
                          "Gracefully shutting down on a %s signal" % name)
            sys.exit(0)

        signal.signal(signal.SIGTERM, terminate)
        signal.signal(signal.SIGINT, terminate)

        if do_main_loop(args.interval, pithos, manifest['log']['object']):
            report.success()
        else:
            report.error("Image creation failed. Check the log for more info")
    finally:
        os.unlink(PID)

# vim: set sta sts=4 shiftwidth=4 sw=4 et ai :

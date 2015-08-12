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

"""This module is responsible for the communication with the icaas service"""

from __future__ import print_function

import requests
import syslog
import json


class Report(object):
    """Report the status to the service"""
    def __init__(self, url, verify=True, log=None):
        """Initialize the class"""
        self.url, token = url.split('#')
        self.verify = verify
        self.log = log
        self.headers = {'Content-type': 'application/json',
                        'x-icaas-token': token}

    def progress(self, details):
        """Report progress"""

        if self.log is not None:
            print(details, file=self.log)

        data = {'status': 'CREATING', 'details': 'agent: %s' % details}
        request = requests.put(self.url, data=json.dumps(data),
                               headers=self.headers, verify=self.verify)
        return request.ok

    def error(self, reason):
        """Report an error"""

        syslog.syslog(syslog.LOG_ERR, reason)

        if self.log is not None:
            print("ERROR:", reason, file=self.log)
        data = {'status': 'ERROR', 'details': 'agent: %s' % reason}
        request = requests.put(self.url, data=json.dumps(data),
                               headers=self.headers, verify=self.verify)
        return request.ok

    def success(self):
        """Report success"""

        if self.log is not None:
            print("Image creation completed!", file=self.log)
        details = 'agent: image creation finished'
        data = {'status': 'COMPLETED', 'details': details}
        request = requests.put(self.url, data=json.dumps(data),
                               headers=self.headers, verify=self.verify)
        return request.ok

# vim: set sta sts=4 shiftwidth=4 sw=4 et ai :

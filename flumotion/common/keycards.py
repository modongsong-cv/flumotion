# -*- Mode: Python; test-case-name: flumotion.test.test_keycards -*-
# vi:si:et:sw=4:sts=4:ts=4

# Flumotion - a streaming media server
# Copyright (C) 2004,2005,2006,2007,2008,2009 Fluendo, S.L.
# Copyright (C) 2010,2011 Flumotion Services, S.A.
# All rights reserved.
#
# This file may be distributed and/or modified under the terms of
# the GNU Lesser General Public License version 2.1 as published by
# the Free Software Foundation.
# This file is distributed without any warranty; without even the implied
# warranty of merchantability or fitness for a particular purpose.
# See "LICENSE.LGPL" in the source distribution for more information.
#
# Headers in this file shall remain intact.

"""
serializable keycards used for authentication
"""

from twisted.cred.credentials import ICredentials
from twisted.spread import pb
from zope.interface import implements

from flumotion.twisted import credentials

__version__ = "$Rev$"
_statesEnum = ['REFUSED', 'REQUESTING', 'AUTHENTICATED']
# state enum values
(REFUSED,
 REQUESTING,
 AUTHENTICATED) = range(3)


class Keycard(pb.Copyable, pb.RemoteCopy):
    """
    I am the base class for keycards which together with credentials are
    a serializable object used in authentication inside Flumotion.

    @ivar  bouncerName: name of the bouncer to authenticate against; set by
                        requester
    @type  bouncerName: str
    @ivar  requesterId: avatarId of the requester
    @type  requesterId: str
    @ivar  avatarId:    avatarId preferred by requester
    @type  avatarId:    str
    @ivar  id:          id of keycard decided by bouncer after authenticating
    @type  id:          object
    @ivar  duration:    duration for which the keycard is valid, or 0 for
                        unlimited
    @type  duration:    int
    @ivar  domain:      requester can pass a domain id to the bouncer
    @type  domain:      str
    @ivar  state:       state the keycard is in
    @type  state:       int
    @ivar  address:     IP address of requester (optional)
    @type  address:     str
    @ivar  username:    username of requester (optional)
    @type  username:    str
    @ivar  password:    password of requester (optional)
    @type  password:    str
    @ivar  path:        path of request (optional)
    @type  path:        str
    @type  token:       token for request (optional)
    @type  token:       str
    @ivar  arguments:   arguments passed with request (optional)
    @type  arguments:   dict of str->str
    """
    implements(ICredentials)

    address = None
    username = None
    password = None
    path = None
    token = ''
    arguments = {}

    def __init__(self):
        self.bouncerName = None
        self.requesterId = None
        self.avatarId = None
        self.id = None
        self.duration = 0
        self.domain = None
        self.state = REQUESTING
        self.arguments = {}

    def getData(self):
        """
        Return a dictionary of the viewable data on the keycard that can be
        used to identify the keycard.
        It doesn't include sensitive information though.

        Subclasses should override to add additional information.
        """
        return {'id': self.id,
                'requester': self.requesterId,
                'domain': self.domain,
                'username': self.username,
                'address': self.address,
                'path': self.path,
                'token': self.token,
                'arguments': self.arguments}

    def __repr__(self):
        return "<%s for requesterId %r in state %s>" % (
            self.__class__.__name__,
            self.requesterId, _statesEnum[self.state])


class KeycardGeneric(Keycard, object):
    pass

pb.setUnjellyableForClass(KeycardGeneric, KeycardGeneric)
# class KeycardUACCP: username, address, crypt password
#       from UsernameCryptPasswordCrypt


UCPP = credentials.UsernameCryptPasswordPlaintext


class KeycardUACPP(Keycard, UCPP):
    """
    I am a keycard with a username, plaintext password and IP address.
    I get authenticated against a crypt password.
    """

    def __init__(self, username, password, address):
        Keycard.__init__(self)
        UCPP.__init__(self, username, password)
        self.address = address

    def getData(self):
        d = Keycard.getData(self)
        d['username'] = self.username
        d['address'] = self.address
        return d

    def __repr__(self):
        return "<%s %s %s@%s for requesterId %r in state %s>" % (
            self.__class__.__name__, self.id, self.username, self.address,
            self.requesterId, _statesEnum[self.state])

pb.setUnjellyableForClass(KeycardUACPP, KeycardUACPP)

# username, address, crypt password
#       from UsernameCryptPasswordCrypt


UCPCC = credentials.UsernameCryptPasswordCryptChallenger


class KeycardUACPCC(Keycard, UCPCC):
    """
    I am a keycard with a username and IP address.
    I get authenticated through challenge/response on a crypt password.
    """

    def __init__(self, username, address):
        Keycard.__init__(self)
        UCPCC.__init__(self, username)
        self.address = address

    def getData(self):
        d = Keycard.getData(self)
        d['username'] = self.username
        d['address'] = self.address
        return d

    def __repr__(self):
        return "<%s %s %s@%s for requesterId %r in state %s>" % (
            self.__class__.__name__, self.id, self.username, self.address,
            self.requesterId, _statesEnum[self.state])

pb.setUnjellyableForClass(KeycardUACPCC, KeycardUACPCC)


class KeycardToken(Keycard, credentials.Token):
    """
    I am a keycard with a token and IP address and a path (optional).
    I get authenticated by token and maybe IP address.
    """

    def __init__(self, token, address, path=None):
        Keycard.__init__(self)
        credentials.Token.__init__(self, token)
        self.address = address
        self.path = path

    def getData(self):
        d = Keycard.getData(self)
        d['token'] = self.token
        d['address'] = self.address
        d['path'] = self.path
        return d

    def __repr__(self):
        return "<%s %s token %s for path %s @%s for reqId %r in state %s>" % (
            self.__class__.__name__, self.id, self.token, self.path,
            self.address, self.requesterId, _statesEnum[self.state])

pb.setUnjellyableForClass(KeycardToken, KeycardToken)


class KeycardHTTPGetArguments(Keycard, credentials.HTTPGetArguments):
    """
    I am a keycard with a token and IP address and a path (optional).
    I get authenticated by HTTP request GET parameters and maybe IP address.

    @type address: C{str}
    @ivar address: The HTTP client IP address.
    @type path: C{str}
    @ivar path: The path requested by the HTTP client.
    """

    def __init__(self, arguments, address, path=None):
        Keycard.__init__(self)
        credentials.HTTPGetArguments.__init__(self, arguments)
        self.address = address
        self.path = path

    def getData(self):
        d = Keycard.getData(self)
        d['arguments'] = self.arguments
        d['address'] = self.address
        d['path'] = self.path
        return d

    def __repr__(self):
        return "<%s %s for path %s @%s for reqId %r in state %s>" % (
            self.__class__.__name__, self.id, self.path,
            self.address, self.requesterId, _statesEnum[self.state])

pb.setUnjellyableForClass(KeycardHTTPGetArguments, KeycardHTTPGetArguments)


USPCC = credentials.UsernameSha256PasswordCryptChallenger


class KeycardUASPCC(Keycard, USPCC):
    """
    I am a keycard with a username and IP address.
    I get authenticated through challenge/response on a SHA-256 password.
    """

    def __init__(self, username, address):
        Keycard.__init__(self)
        USPCC.__init__(self, username)
        self.address = address

    def getData(self):
        d = Keycard.getData(self)
        d['username'] = self.username
        d['address'] = self.address
        return d

    def __repr__(self):
        return "<%s %s %s@%s for requesterId %r in state %s>" % (
            self.__class__.__name__, self.id, self.username, self.address,
            self.requesterId, _statesEnum[self.state])

pb.setUnjellyableForClass(KeycardUASPCC, KeycardUASPCC)


class KeycardHTTPDigest(Keycard, credentials.HTTPDigestChallenger):

    def __init__(self, username):
        Keycard.__init__(self)
        credentials.HTTPDigestChallenger.__init__(self, username)

    def getData(self):
        d = Keycard.getData(self)
        d['username'] = self.username
        # Realm? Uri?
        return d

    def __repr__(self):
        return "<%s %s %s for requesterId %r in state %s>" % (
            self.__class__.__name__, self.id, self.username,
            self.requesterId, _statesEnum[self.state])

pb.setUnjellyableForClass(KeycardHTTPDigest, KeycardHTTPDigest)

# -*- Mode: Python -*-
# vi:si:et:sw=4:sts=4:ts=4
#
# Flumotion - a video streaming server
# Copyright (C) 2004 Fluendo
#
# admin.py
# 
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
# 
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
# 
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 59 Temple Street #330, Boston, MA 02111-1307, USA.

from twisted.internet import reactor
from twisted.spread import pb

from flumotion.twisted import errors, pbutil
from flumotion.utils import log

class ComponentView(pb.Copyable):
    """
    I present state of a component through a L{RemoteComponentView} in the peer.
    I get the state I present from a L{controller.ComponentAvatar}.
    I live in the controller.
    """
    def __init__(self, component):
        """
        @type component: L{server.controller.ComponentAvatar}
        """
        self.name = component.getName()
        # forced to int so it's jellyable
        self.state = int(component.state)
        self.sources = component.getSources()
        self.feeds = component.getFeeds()
        self.options = component.options.dict

class RemoteComponentView(pb.RemoteCopy):
    """
    I represent state of a component.
    I am a copy of a controller-side L{ComponentView}
    I live in an admin client.
    """
    def __cmp__(self, other):
        if not isinstance(other, RemoteComponentView):
            return False
        return cmp(self.name, other.name)
    
    def __repr__(self):
        return '<RemoteComponentView %s>' % self.name
pb.setUnjellyableForClass(ComponentView, RemoteComponentView)

class AdminAvatar(pb.Avatar, log.Loggable):
    """
    I am an avatar created for an administrative client interface.
    A reference to me is given (for example, to gui.AdminInterface)
    when logging in and requesting an "admin" avatar.
    I live in the controller.
    """
    logCategory = 'admin-avatar'
    def __init__(self, admin):
        """
        @type admin: L{server.admin.Admin}
        """
        self.admin = admin
        # FIXME: use accessor to get controller ?
        self.controller = admin.controller
        self.mind = None
        self.debug("created new AdminAvatar")
        
    def hasRemoteReference(self):
        """
        Check if the avatar has a remote reference to the peer.

        @rtype: boolean
        """
        return self.mind != None
    
    def _mindCallRemote(self, name, *args, **kwargs):
        if not self.hasRemoteReference():
            self.warning("Can't call remote method %s, no mind" % name)
            return
        
        # we can't do a .debug here, since it will trigger a resend of the
        # debug message as well, causing infinite recursion !
        # self.debug('Calling remote method %s%r' % (name, args))
        try:
            return self.mind.callRemote(name, *args, **kwargs)
        except pb.DeadReferenceError:
            mind = self.mind
            self.mind = None
            self.warning("mind %s is a dead reference, removing" % mind)
            return
        
    # FIXME: this should probably be renamed to getComponents ?
    def getClients(self):
        """
        Return all components logged in to the controller.
        
        @rtype: C{list} of L{server.admin.ComponentView}
        """
        # FIXME: should we use an accessor to get at components from c ?
        clients = map(ComponentView, self.controller.components.values())
        return clients

    def sendLog(self, category, type, message):
        """
        Send the given log message to the peer.
        """
        # don't send if we don't have a remote reference yet.
        # this avoids recursion from the remote caller trying to warn
        if self.hasRemoteReference():
            self._mindCallRemote('log', category, type, message)
        
    def componentAdded(self, component):
        """
        Tell the avatar that a component has been added.
        """
        self.debug("AdminAvatar.componentAdded: %s" % component)
        self._mindCallRemote('componentAdded', ComponentView(component))
        
    def componentRemoved(self, component):
        """
        Tell the avatar that a component has been removed.
        """
        self.debug("AdminAvatar.componentRemoved: %s" % component)
        self._mindCallRemote('componentRemoved', ComponentView(component))

    def uiStateChanged(self, name, state):
        self.debug("AdminAvatar.uiStateChanged: %s %s" % (name, state))
        self._mindCallRemote('uiStateChanged', name, state)

    def attached(self, mind):
        """
        Give the avatar a remote reference to the
        peer's client that logged in and requested the avatar.
        Also make the avatar send the initial clients to the peer.

        @type mind: L{twisted.spread.pb.RemoteReference}
        """
        self.mind = mind
        ip = self.mind.broker.transport.getPeer()[1]
        self.debug('Client from %s attached, sending client components' % ip)
        self.log('Client attached is mind %s' % mind)

        self._mindCallRemote('initial', self.getClients())

    def detached(self, mind):
        """
        Tell the avatar that the peer's client referenced by the mind
        has detached.
        """
        assert(self.mind == mind)
        ip = self.mind.broker.transport.getPeer()[1]
        self.mind = None
        self.debug('Client from %s detached' % ip)
        self.log('Client detached is mind %s' % mind)
        self.admin.removeAvatar(self)

    ### pb.Avatar IPerspective methods
    def perspective_shutdown(self):
        print 'SHUTTING DOWN'
        reactor.stop()
        raise SystemExit

    # Generic interface to call into a component
    def perspective_callComponentRemote(self, component_name, method_name,
                                        *args, **kwargs):
        component = self.controller.getComponent(component_name)
        try:
            return component.callComponentRemote(method_name, *args, **kwargs)
        except Exception, e:
            self.warning(str(e))
            raise
        
    def perspective_setComponentElementProperty(self, component_name, element, property, value):
        """Set a property on an element in a component."""
        component = self.controller.getComponent(component_name)
        try:
            return component.setElementProperty(element, property, value)
        except errors.PropertyError, exception:
            self.warning(str(exception))
            raise

    def perspective_getComponentElementProperty(self, component_name, element, property):
        """Get a property on an element in a component."""
        component = self.controller.getComponent(component_name)
        try:
            return component.getElementProperty(element, property)
        except errors.PropertyError, exception:
            self.warning(str(exception))
            raise

    def perspective_getUIEntry(self, component_name):
        component = self.controller.getComponent(component_name)
        try:
            return component.getUIEntry()
        except Exception, e:
            self.warning(str(e))
            raise

    def perspective_reloadComponent(self, component_name):
        """Reload modules in the given component."""
        component = self.controller.getComponent(component_name)
        return component.reloadComponent()

    def perspective_reloadController(self):
        """Reload modules in the controller."""
        import sys
        from twisted.python.rebuild import rebuild
        # reload ourselves first
        rebuild(sys.modules[__name__])

        # now rebuild relevant modules
        import flumotion.utils
        rebuild(sys.modules['flumotion.utils'])
        flumotion.utils.reload()
        self._reloaded()

    # separate method so it runs the newly reloaded one :)
    def _reloaded(self):
        self.info('reloaded module code for %s' % __name__)

class Admin(pb.Root):
    """
    I interface between the Controller and administrative clients.
    For each client I create an L{AdminAvatar} to handle requests.
    I live in the controller.
    """
    def __init__(self, controller):
        """
        @type controller: L{server.controller.Controller}
        """
        self.controller = controller
        self.clients = [] # all AdminAvatars we instantiate
        log.addLogHandler(self.logHandler)
        self.logcache = []

    # FIXME: Loggable
    debug = lambda s, *a: log.debug('admin', *a)
        
    def logHandler(self, category, type, message):
        self.logcache.append((category, type, message))
        for client in self.clients:
            client.sendLog(category, type, message)

    def sendCache(self, client):
        if not client.hasRemoteReference():
            reactor.callLater(0.25, self.sendCache, client)
            return
        
        self.debug('sending logcache to client (%d messages)' % len(self.logcache))
        for category, type, message in self.logcache:
            client.sendLog(category, type, message)
        
    def getAvatar(self):
        """
        Creates a new administration avatar.
        @rtype:   L{server.admin.AdminAvatar}
        @returns: a new avatar for the admin client.
        """
        self.debug('creating new AdminAvatar')
        avatar = AdminAvatar(self)
        reactor.callLater(0.25, self.sendCache, avatar)
        
        self.clients.append(avatar)
        return avatar

    def removeAvatar(self, avatar):
        """
        Removes the AdminAvatar from our list of avatars.
        @type avatar: L{server.admin.AdminAvatar}
        """
        self.debug('removing AdminAvatar %s' % avatar)
        self.clients.remove(avatar)
    
    def componentAdded(self, component):
        """
        Tell all created AdminAvatars that a component was added.

        @type component: L{server.controller.ComponentAvatar}
        """
        for client in self.clients:
            client.componentAdded(component)

    def componentRemoved(self, component):
        """
        Tell all created AdminAvatars that a component was removed.

        @type component: L{server.controller.ComponentAvatar}
        """
        for client in self.clients:
            client.componentRemoved(component)

    def uiStateChanged(self, name, state):
        """
        Tell all created AdminAvatars that an ui state for a component was changed

        @type name:      name of the component
        @type state:     new ui state
        """
        
        for client in self.clients:
            client.uiStateChanged(name, state)
        

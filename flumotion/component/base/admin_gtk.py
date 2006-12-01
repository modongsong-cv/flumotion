# -*- Mode: Python -*-
# vi:si:et:sw=4:sts=4:ts=4
#
# Flumotion - a streaming media server
# Copyright (C) 2004,2005,2006 Fluendo, S.L. (www.fluendo.com).
# All rights reserved.

# This file may be distributed and/or modified under the terms of
# the GNU General Public License version 2 as published by
# the Free Software Foundation.
# This file is distributed without any warranty; without even the implied
# warranty of merchantability or fitness for a particular purpose.
# See "LICENSE.GPL" in the source distribution for more information.

# Licensees having purchased or holding a valid Flumotion Advanced
# Streaming Server license may use this file in accordance with the
# Flumotion Advanced Streaming Server Commercial License Agreement.
# See "LICENSE.Flumotion" in the source distribution for more information.

# Headers in this file shall remain intact.

"""
Base classes for component UI's using GTK+
"""

import os

import gtk
import gtk.glade

from twisted.python import util
from twisted.internet import defer

from flumotion.common import errors, log, common
from flumotion.twisted import flavors
from flumotion.twisted.defer import defer_generator_method
from flumotion.twisted.compat import implements

class BaseAdminGtk(log.Loggable):
    """
    I am a base class for all GTK+-based Admin views.
    I am a view on one component's properties.

    @type nodes: L{twisted.python.util.OrderedDict}
    @ivar nodes: an ordered dict of name -> L{BaseAdminGtkNode}
    """

    logCategory = "admingtk"
    
    def __init__(self, state, admin):
        """
        @param state: state of component this is a UI for
        @type  state: L{flumotion.common.planet.AdminComponentState}
        @type  admin: L{flumotion.admin.admin.AdminModel}
        @param admin: the admin model that interfaces with the manager for us
        """
        self.state = state
        self.name = state.get('name')
        self.admin = admin
        self.debug('creating admin gtk for state %r' % state)
        self.uiState = None
        self.nodes = util.OrderedDict()

        d = admin.componentCallRemote(state, 'getUIState')
        d.addCallback(self.setUIState)
        
    def setUIState(self, state):
        self.debug('starting listening to state %r', state)
        state.addListener(self, self.stateSet, self.stateAppend,
                          self.stateRemove)
        self.uiState = state
        for node in self.getNodes().values():
            node.gotUIState(state)
        self.uiStateChanged(state)

    def propertyErrback(self, failure, window):
        failure.trap(errors.PropertyError)
        self.warning("%s." % failure.getErrorMessage())
        #window.error_dialog("%s." % failure.getErrorMessage())
        return None

    def setElementProperty(self, elementName, propertyName, value):
        """
        Set the given property on the element with the given name.
        """
        d = self.admin.setProperty(self.state, elementName, propertyName, value)
        d.addErrback(self.propertyErrback, self)
        return d
    
    def getElementProperty(self, elementName, propertyName):
        """
        Get the value of the given property of the element with the given name.
        
        Returns: L{twisted.internet.defer.Deferred} returning the value.
        """
        d = self.admin.getProperty(self.state, elementName, propertyName)
        d.addErrback(self.propertyErrback, self)
        return d

    def callRemote(self, methodName, *args, **kwargs):
        return self.admin.componentCallRemote(self.state, methodName,
                                              *args, **kwargs)
        
    def propertyChanged(self, name, value):
        """
        Override this method to be notified of component's properties that
        have changed.

        I am meant to be overridden.
        """
        self.debug("property %s changed to %r" % (name, value))

    # FIXME: .setup() is subclassable, while .render() on nodes has
    # haveWidgetTree.  choose one of the two patterns in general
    def setup(self):
        """
        Set up the admin view so it can display nodes.
        """
        self.debug('BaseAdminGtk.setup()')

        self.nodes['Plumbing'] = PlumbingAdminGtkNode(self.state,
                                                      self.admin)

        # set up translations
        if not hasattr(self, 'gettext_domain'):
            yield None

        lang = common.getLL()
        self.debug("loading bundle for %s locales" % lang)
        bundleName = '%s-locale-%s' % (self.gettext_domain, lang)
        d = self.admin.bundleLoader.getBundleByName(bundleName)
        yield d

        try:
            localedatadir = d.value()
        except errors.NoBundleError:
            self.debug("Failed to find locale bundle %s" % bundleName)
            yield None

        localeDir = os.path.join(localedatadir, 'locale')
        self.debug("Loading locales for %s from %s" % (
            self.gettext_domain, localeDir))
        gettext.bindtextdomain(self.gettext_domain, localeDir)
        gtk.glade.bindtextdomain(self.gettext_domain, localeDir)
        yield None
    setup = defer_generator_method(setup)

    def getNodes(self):
        """
        Return a dict of admin UI nodes.
        """
        return self.nodes

    # FIXME: deprecated
    def render(self):
        """
        Render the GTK+ admin view for this component and return the
        main widget for embedding.
        """
        raise NotImplementedError

    def uiStateChanged(self, stateObject):
        # so, this is still here, but I'd prefer people to (1) just use
        # the nodes and not the global admin; and (2) use the state
        # listener stuff more than the chunkier 'uistatechanged'
        pass

    def stateSet(self, object, key, value):
        self.uiStateChanged(object)

    def stateAppend(self, object, key, value):
        self.uiStateChanged(object)

    def stateRemove(self, object, key, value):
        self.uiStateChanged(object)

class BaseAdminGtkNode(log.Loggable):
    """
    I am a base class for all GTK+-based Admin UI nodes.
    I am a view on a set of properties for a component.

    @ivar widget: the main widget representing this node
    @type widget: L{gtk.Widget}
    @ivar wtree:  the widget tree representation for this node
    """

    implements(flavors.IStateListener)

    logCategory = "admingtk"
    glade_file = None
    gettext_domain = 'flumotion'

    def __init__(self, state, admin, title=None):
        """
        @param state: state of component this is a UI node for
        @type  state: L{flumotion.common.planet.AdminComponentState}
        @param admin: the admin model that interfaces with the manager for us
        @type  admin: L{flumotion.admin.admin.AdminModel}
        @param title: the (translated) title to show this node with
        @type  title: str
        """
        self.state = state
        self.admin = admin
        self.statusbar = None
        self.title = title
        self.nodes = util.OrderedDict()
        self.wtree = None
        self.widget = None
        self.uiState = None
        
    def status_push(self, str):
        if self.statusbar:
            return self.statusbar.push('notebook', str)

    def status_pop(self, mid):
        if self.statusbar:
            return self.statusbar.remove('notebook', mid)

    def propertyErrback(self, failure, window):
        failure.trap(errors.PropertyError)
        self.warning("%s." % failure.getErrorMessage())
        #window.error_dialog("%s." % failure.getErrorMessage())
        return None

    def setElementProperty(self, elementName, propertyName, value):
        """
        Set the given property on the element with the given name.
        """
        d = self.admin.setProperty(self.state, elementName, propertyName, value)
        d.addErrback(self.propertyErrback, self)
        return d
    
    def getElementProperty(self, elementName, propertyName):
        """
        Get the value of the given property of the element with the given name.
        
        Returns: L{twisted.internet.defer.Deferred} returning the value.
        """
        d = self.admin.getProperty(self.state, elementName, propertyName)
        d.addErrback(self.propertyErrback, self)
        return d

    def callRemote(self, methodName, *args, **kwargs):
        return self.admin.componentCallRemote(self.state, methodName,
                                              *args, **kwargs)
       
    # FIXME: do this automatically if there is a gladeFile class attr set
    def loadGladeFile(self, gladeFile, domain='flumotion'):
        """
        Returns: a deferred returning the widget tree from the glade file.
        """
        def _getBundledFileCallback(result, gladeFile):
            path = result
            if not os.path.exists(path):
                self.warning("Glade file %s not found in path %s" % (
                    gladeFile, path))
            self.debug("Switching glade text domain to %s" % domain)
            self.debug("loading widget tree from %s" % path)
            old = gtk.glade.textdomain()
            gtk.glade.textdomain(domain)
            self.wtree = gtk.glade.XML(path)
            self.debug("Switching glade text domain back to %s" % old)
            gtk.glade.textdomain(old)
            return self.wtree

        self.debug("requesting bundle for glade file %s" % gladeFile)
        d = self.admin.bundleLoader.getFile(gladeFile)
        d.addCallback(_getBundledFileCallback, gladeFile)
        return d

    def getWidget(self, name):
        if not self.wtree:
            raise IndexError
        widget = self.wtree.get_widget(name)
        if not widget:
            self.warning('Could not get widget %s' % name)

        return widget

    def haveWidgetTree(self):
        """
        I am called when the widget tree has been gotten from the glade
        file. Responsible for setting self.widget.

        Override me to act on it.
        """
        pass

    def propertyChanged(self, name, value):
        """
        I am meant to be overridden.
        """
        self.debug("property %s changed to %r" % (name, value))

    def gotUIState(self, state):
        self.uiState = state
        if self.widget:
            self.setUIState(self.uiState)

    def setUIState(self, state):
        """
        Called by the BaseAdminGtk when it gets the UI state and the GUI
        is ready. Chain up if you provide your own implementation.
        """
        self.uiState = state
        state.addListener(self, self.stateSet, self.stateAppend,
                          self.stateRemove, self.stateSetItem,
                          self.stateDelItem)

    def stateSet(self, state, key, value):
        "Override me"
        pass

    def stateAppend(self, state, key, value):
        "Override me"
        pass

    def stateRemove(self, state, key, value):
        "Override me"
        pass
    
    def stateSetItem(self, state, key, subkey, value):
        "Override me"
        pass
    
    def stateDelItem(self, state, key, subkey, value):
        "Override me"
        pass

    def render(self):
        """
        Render the GTK+ admin view for this component.
        
        Returns: a deferred returning the main widget for embedding
        """
        if self.glade_file:
            self.debug('render: loading glade file %s in text domain %s' % (
                self.glade_file, self.gettext_domain))
            dl = self.loadGladeFile(self.glade_file, self.gettext_domain)
            yield dl

            try:
                self.wtree = dl.value()
            except RuntimeError:
                msg = 'Could not load glade file %s' % self.glade_file
                self.warning(msg)
                yield gtk.Label("%s.  Kill the programmer." % msg)

            self.debug('render: calling haveWidgetTree')
            self.haveWidgetTree()
            
        if not self.widget:
            self.debug('render: no self.widget, failing')
            yield defer.fail(IndexError)
            
        if self.uiState:
            self.debug('calling setUIState on the node')
            self.setUIState(self.uiState)

        self.debug('render: yielding widget %s' % self.widget)
        yield self.widget
    render = defer_generator_method(render)

# this class is a bit of an experiment, and is private, dudes and ladies
class StateWatcher(object):
    def __init__(self, state, setters, appenders, removers):
        self.state = state
        self.setters = setters
        self.appenders = appenders
        self.removers = removers
        self.shown = False

        state.addListener(self, set=self.onSet, append=self.onAppend,
                          remove=self.onRemove)
        for k in appenders:
            for v in state.get(k):
                self.onAppend(state, k, v)

    def hide(self):
        if self.shown:
            for k in self.setters:
                self.onSet(self.state, k, None)
            self.shown = False

    def show(self):
        if not self.shown:
            self.shown = True
            for k in self.setters:
                self.onSet(self.state, k, self.state.get(k))

    def onSet(self, obj, k, v):
        if self.shown and k in self.setters:
            self.setters[k](self.state, v)

    def onAppend(self, obj, k, v):
        if k in self.appenders:
            self.appenders[k](self.state, v)

    def onRemove(self, obj, k, v):
        if k in self.removers:
            self.removers[k](self.state, v)

    def unwatch(self):
        if self.state:
            self.hide()
            for k in self.removers:
                for v in self.state.get(k):
                    self.onRemove(self.state, k, v)
            self.state.removeListener(self)
            self.state = None

class PlumbingAdminGtkNode(BaseAdminGtkNode):
    glade_file = os.path.join('flumotion', 'component', 'base',
                              'plumbing.glade')

    def __init__(self, state, admin):
        BaseAdminGtkNode.__init__(self, state, admin, 'Plumbing')
        self.treemodel = None
        self.treeview = None
        self.selected = None
        self.labels = {}

    def select(self, watcher):
        if self.selected:
            self.selected.hide()
        if watcher:
            self.selected = watcher
            self.selected.show()
        else:
            self.selected = None

    def setFeederName(self, state, value):
        self.labels['feeder-name'].set_markup('Feeder <b>%s</b>' % value)

    def setFeederClientName(self, state, value):
        self.labels['feeder-name'].set_markup('Feeding to <b>%s</b>'
                                              % value)
    def setFeederClientBytesRead(self, state, value):
        txt = value and (common.formatStorage(value)+'Byte') or ''
        self.labels['feeder-client-bytesread'].set_text(txt)
    def setFeederClientBuffersDropped(self, state, value):
        self.labels['feeder-client-buffersdropped'].set_text(str(value))

    def addFeeder(self, uiState, state):
        feederId = state.get('feedId')
        i = self.treemodel.append(None)
        self.treemodel.set(i, 0, feederId, 1, state)
        w = StateWatcher(state,
                         {'feedId': self.setFeederName},
                         {'clients': self.addFeederClient},
                         {'clients': self.removeFeederClient})
        self.treemodel.set(i, 2, w)
        self.treeview.expand_all()

    def addFeederClient(self, feederState, state):
        clientId = state.get('clientId')
        for row in self.treemodel:
            if self.treemodel.get_value(row.iter, 1) == feederState:
                break
        i = self.treemodel.append(row.iter)
        self.treemodel.set(i, 0, clientId, 1, state)
        w = StateWatcher(state,
                         {'clientId': self.setFeederClientName,
                          'bytesRead': self.setFeederClientBytesRead,
                          'buffersDropped':
                          self.setFeederClientBuffersDropped},
                         {},
                         {})
        self.treemodel.set(i, 2, w)
        self.treeview.expand_all()

    def removeFeederClient(self, feederState, state):
        for row in self.treemodel.iter_children(None):
            if self.treemodel.get_value(row.iter, 1) == feederState:
                break
        for row in row.iterchildren():
            if self.treemodel.get_value(row.iter, 1) == state:
                break
        state, watcher = self.treemodel.get(i, 1, 2)
        if watcher == self.selected:
            self.select(None)
        watcher.unwatch()
        self.treemodel.remove(i)

    def setUIState(self, state):
        # will only be called when we have a widget tree
        BaseAdminGtkNode.setUIState(self, state)
        self.widget.show_all()
        for feeder in state.get('feeders'):
            self.addFeeder(state, feeder)

    def haveWidgetTree(self):
        self.labels = {}
        self.widget = self.wtree.get_widget('plumbing-widget')
        self.treeview = self.wtree.get_widget('treeview-feeders')
        self.treemodel = gtk.TreeStore(str, object, object)
        self.treeview.set_model(self.treemodel)
        col = gtk.TreeViewColumn('Feeder', gtk.CellRendererText(),
                                 text=0)
        self.treeview.append_column(col)
        sel = self.treeview.get_selection()
        sel.set_mode(gtk.SELECTION_SINGLE)
        def sel_changed(sel):
            model, i = sel.get_selected()
            self.select(i and model.get_value(i, 2))
        sel.connect('changed', sel_changed)
        def set_label(name):
            self.labels[name] = self.wtree.get_widget('label-'+name)
            self.labels[name].set_text('')
        for type in ('name',):
            set_label('feeder-' + type)
        for type in ('bytesread', 'buffersdropped'):
            set_label('feeder-client-' + type)
        self.widget.show_all()
        return self.widget

class EffectAdminGtkNode(BaseAdminGtkNode):
    """
    I am a base class for all GTK+-based component effect Admin UI nodes.
    I am a view on a set of properties for an effect on a component.
    """
    def __init__(self, state, admin, effectName, title=None):
        """
        @param state: state of component this is a UI for
        @type  state: L{flumotion.common.planet.AdminComponentState}
        @param admin: the admin model that interfaces with the manager for us
        @type  admin: L{flumotion.admin.admin.AdminModel}
        """
        BaseAdminGtkNode.__init__(self, state, admin, title)
        self.effectName = effectName

    def effectCallRemote(self, methodName, *args, **kwargs):
        return self.admin.componentCallRemote(self.state,
            "effect", self.effectName, methodName, *args, **kwargs)

# -*- Mode: Python; test-case-name:flumotion.test.test_worker_worker -*-
# vi:si:et:sw=4:sts=4:ts=4
#
# Flumotion - a streaming media server
# Copyright (C) 2004,2005 Fluendo, S.L. (www.fluendo.com). All rights reserved.

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
worker-side objects to handle worker clients
"""

import errno
import os
import signal
import sys

import gst
import gst.interfaces

from twisted.cred import portal
from twisted.internet import defer, protocol, reactor
from twisted.spread import pb
import twisted.cred.error
import twisted.internet.error

from flumotion.common import errors, interfaces, log, bundleclient, common
from flumotion.twisted import checkers
from flumotion.twisted import pb as fpb
from flumotion.worker import job
from flumotion.configure import configure

factoryClass = fpb.ReconnectingFPBClientFactory
class WorkerClientFactory(factoryClass):
    """
    I am a client factory for the worker to log in to the manager.
    """
    logCategory = 'worker'
    def __init__(self, brain):
        """
        @type brain: L{flumotion.worker.worker.WorkerBrain}
        """
        self.manager_host = brain.manager_host
        self.manager_port = brain.manager_port
        self.medium = brain.medium
        # doing this as a class method triggers a doc error
        factoryClass.__init__(self)
        # maximum 10 second delay for workers to attempt to log in again
        self.maxDelay = 10
        
    def startLogin(self, keycard):
        factoryClass.startLogin(self, keycard, self.medium,
            interfaces.IWorkerMedium)
        
    def gotDeferredLogin(self, d):
        d.addCallback(self._loginCallback)
        d.addErrback(self._accessDeniedErrback)
        d.addErrback(self._connectionRefusedErrback)
        d.addErrback(self._loginFailedErrback)
            
    def _loginCallback(self, reference):
        self.info("Logged in to manager")
        self.debug("remote reference %r" % reference)
        self.setRemoteReference(reference)

    def setRemoteReference(self, remoteReference):
        self.medium.setRemoteReference(remoteReference)
        remoteReference.notifyOnDisconnect(self._remoteDisconnected)

    def _remoteDisconnected(self, remoteReference):
        self.warning('Lost connection to manager, will attempt to reconnect')

    def _accessDeniedErrback(self, failure):
        failure.trap(twisted.cred.error.UnauthorizedLogin)
        self.error('Access denied.')
        
    def _connectionRefusedErrback(self, failure):
        failure.trap(twisted.internet.error.ConnectionRefusedError)
        self.error('Connection to %s:%d refused.' % (self.manager_host,
                                                     self.manager_port))
                                                      
    def _loginFailedErrback(self, failure):
        self.error('Login failed, reason: %s' % str(failure))

    # override log.Loggable method so we don't traceback
    def error(self, message):
        self.warning('Shutting down worker because of error:')
        self.warning(message)
        print >> sys.stderr, 'ERROR: %s' % message
        reactor.stop()

class WorkerMedium(pb.Referenceable, log.Loggable):
    """
    I am a medium interfacing with the manager-side WorkerAvatar.
    """
    
    logCategory = 'workermedium'

    __implements__ = interfaces.IWorkerMedium,
    
    def __init__(self, brain):
        self.brain = brain
        self.remote = None
        self.bundleLoader = None
        
    def cb_processFinished(self, *args):
        self.debug('processFinished %r' % args)

    def cb_processFailed(self, *args):
        self.debug('processFailed %r' % args)

    ### IMedium methods
    def setRemoteReference(self, remoteReference):
        self.debug('setRemoteReference: %r' % remoteReference)
        self.remote = remoteReference
        self.bundleLoader = bundleclient.BundleLoader(self.remote)

    def hasRemoteReference(self):
        return self.remote != None

    ### pb.Referenceable method for the manager's WorkerAvatar
    def remote_start(self, avatarId, type, config):
        """
        Start a component of the given type with the given config.

        @param avatarId: type of the component to start
        @type  avatarId: string
        @param type:     type of the component to start
        @type  type:     string
        @param config:   a configuration dictionary for the component
        @type  config:   dict

        @returns: a deferred fired when the process has started
        """
        self.info('Starting component "%s" of type "%s"' % (avatarId, type))
        self.debug('remote_start(): id %s, type %s, config %r' % (
            avatarId, type, config))

        # create a deferred that will be triggered when the jobavatar
        # instructs the job to start a component
        d = self.brain.deferredStartCreate(avatarId)

        if d:
            d.addCallback(self._deferredStartCallback, avatarId)
            self.brain.kindergarten.play(avatarId, type, config)
            return d
        else:
            msg = ('Component "%s" has already received a start request'
                   % avatarId)
            raise errors.ComponentStart(msg)

    def _deferredStartCallback(self, result, avatarId):
        self.debug('deferred start for %s fired, remote_start returns %s' % (
            avatarId, result))
        return result

    def remote_checkElements(self, elementNames):
        """
        Checks if one or more GStreamer elements are present and can be
        instantiated.

        @param elementNames:   names of the Gstreamer elements
        @type  elementNames:   list of strings

        @rtype:   list of strings
        @returns: a list of instantiatable element names
        """
        self.debug('remote_checkElements: element names to check %r' % (
            elementNames,))

        list = [name for name in elementNames
                         if gst.element_factory_make(name) != None]
        self.debug('remote_checkElements: returning elements names %r' % list)
        return list

    def remote_runProc(self, module, function, *args, **kwargs):
        """
        Runs the given function in the given module with the given arguments.
        
        @param module:   module the function lives in
        @type  module:   string
        @param function: function to run
        @type  function: string

        @returns: the return value of the given function in the module.
        """
        def got_module(result):
            self.debug('got module, running %s' % function)
            try:
                proc = getattr(result, function)
                return proc(*args, **kwargs)
            except Exception, e:
                msg = 'runCode(%s, %s) failed: %s raised: %s' % (
                    module, function, e.__class__.__name__, " ".join(e.args))
                self.warning(msg)
                raise errors.RemoteRunError(msg)
            except:
                msg = 'runCode(%s, %s) failed: Unknown exception' % (
                    module, function)
                self.warning(msg)
                raise errors.RemoteRunError(msg)

        self.debug('loading module %s for function %s' % (module, function))
        d = self.bundleLoader.load(module)
        d.addCallback(got_module)
        return d

class Kid:
    """
    I am an abstraction of a job process started by the worker.
    """
    def __init__(self, pid, avatarId, type, config):
        self.pid = pid 
        self.avatarId = avatarId
        self.type = type
        self.config = config

    # pid = protocol.transport.pid
    def getPid(self):
        return self.pid

class Kindergarten(log.Loggable):
    """
    I spawn job processes.
    I live in the worker brain.
    """

    logCategory = 'workerbrain' # thomas: I don't like Kindergarten

    def __init__(self, options):
        """
        @param options: the optparse option instance of command-line options
        @type  options: dict
        """
        dirname = os.path.split(os.path.abspath(sys.argv[0]))[0]
        self.program = os.path.join(dirname, 'flumotion-worker')
        self.kids = {} # avatarId -> Kid
        self.options = options
        
    def play(self, avatarId, type, config):
        """
        Create a kid and make it "play" by starting a job.
        Starts a component with the given name, of the given type, and
        the given config dictionary.

        @param avatarId: avatarId the component should use to log in
        @type  avatarId: string
        @param type:     type of component to start
        @type  type:     string
        @param config:   a configuration dictionary for the component
        @type  config:   dict
        """
        
        # This forks and returns the pid
        pid = job.run(avatarId, self.options)
        
        self.kids[avatarId] = Kid(pid, avatarId, type, config)

    def getKid(self, avatarId):
        return self.kids[avatarId]
    
    def getKids(self):
        return self.kids.values()

    def removeKidByPid(self, pid):
        """
        Remove the kid from the kindergarten based on the pid.
        Called by the signal handler in the brain.

        @returns: whether or not a kid with that pid was removed
        @rtype: boolean
        """
        for path, kid in self.kids.items():
            if kid.getPid() == pid:
                self.debug('Removing kid with name %s and pid %d' % (
                    path, pid))
                del self.kids[path]
                return True

        self.warning('Asked to remove kid with pid %d but not found' % pid)
        return False

# Similar to Vishnu, but for worker related classes
class WorkerBrain(log.Loggable):
    """
    I manage jobs and everything related.
    I live in the main worker process.
    """

    logCategory = 'workerbrain'

    def __init__(self, options):
        """
        @param options: the optparsed dictionary of command-line options
        @type  options: an object with attributes
        """
        self._oldSIGCHLDHandler = None # stored by installSIGCHLDHandler
        self._oldSIGTERMHandler = None # stored by installSIGTERMHandler
        self.options = options

        signal.signal(signal.SIGINT, signal.SIG_IGN)

        self.manager_host = options.host
        self.manager_port = options.port
        self.manager_transport = options.transport

        self.workerName = options.name
        
        self.kindergarten = Kindergarten(options)
        self.job_server_factory, self.job_heaven = self.setup()

        self.medium = WorkerMedium(self)
        self.worker_client_factory = WorkerClientFactory(self)

        self._startDeferreds = {}

    def login(self, keycard):
        self.worker_client_factory.startLogin(keycard)
                             
    def setup(self):
        # called from Init
        root = JobHeaven(self, self.options.feederports)
        dispatcher = JobDispatcher(root)
        # FIXME: we should hand a username and password to log in with to
        # the job process instead of allowing anonymous
        checker = checkers.FlexibleCredentialsChecker()
        checker.allowPasswordless(True)
        p = portal.Portal(dispatcher, [checker])
        job_server_factory = pb.PBServerFactory(p)
        reactor.listenUNIX(job.getSocketPath(), job_server_factory)

        return job_server_factory, root

    # override log.Loggable method so we don't traceback
    def error(self, message):
        self.warning('Shutting down worker because of error:')
        self.warning(message)
        print >> sys.stderr, 'ERROR: %s' % message
        reactor.stop()

    def installSIGCHLDHandler(self):
        """
        Install our own signal handler for SIGCHLD.
        This will call the currently installed one first, then reap
        any leftover zombies.
        """
        self.debug("Installing SIGCHLD handler")
        handler = signal.signal(signal.SIGCHLD, self._SIGCHLDHandler)
        if handler not in (signal.SIG_IGN, signal.SIG_DFL, None):
            self._oldSIGCHLDHandler = handler

    def _SIGCHLDHandler(self, signal, frame):
        self.debug("handling SIGCHLD")
        if self._oldSIGCHLDHandler:
            self.debug("calling Twisted handler")
            self._oldSIGCHLDHandler(signal, frame)
            
        # we could have been called for more than one kid, so handle all
        reaped = False

        while True:
            # find a pid that needs reaping
            # only allow ECHILD to pass, which means no children needed reaping
            pid = 0
            try:
                self.debug('calling os.waitpid to reap children')
                pid, status = os.waitpid(-1, os.WNOHANG)
                self.debug('os.waitpid() returned pid %d' % pid)
                reaped = True
            except OSError, e:
                if not e.errno == errno.ECHILD:
                    raise
                
            # check if we reaped a child or not
            # if we reaped none at all in this handling, something shady went
            # on, so we info
            if not pid:
                if not reaped:
                    self.info('No children of mine to wait on')
                else:
                    self.debug('Done reaping children')
                return

            # we reaped, so ...
            # remove from the kindergarten
            self.kindergarten.removeKidByPid(pid)

            # check if it exited nicely; see Stevens
            if os.WIFEXITED(status):
                retval = os.WEXITSTATUS(status)
                self.info("Reaped child job with pid %d, exit value %d" % (
                    pid, retval))
            elif os.WIFSIGNALED(status):
                signal = os.WTERMSIG(status)
                self.info(
                    "Reaped job child with pid %d signaled by signal %d" % (
                        pid, signal))
                if os.WCOREDUMP(status):
                    self.info("Core dumped")
                    
            elif os.WIFSTOPPED(status):
                signal = os.WSTOPSIG(status)
                self.info(
                    "Reaped job child with pid %d stopped by signal %d" % (
                        pid, signal))
            else:
                self.info(
                    "Reaped job child with pid %d and unhandled status %d" % (
                        pid, status))

    def installSIGTERMHandler(self):
        """
        Install our own signal handler for SIGTERM.
        This will call the currently installed one first, then shut down
        jobs.
        """
        self.debug("Installing SIGTERM handler")
        handler = signal.signal(signal.SIGTERM, self._SIGTERMHandler)
        if handler not in (signal.SIG_IGN, signal.SIG_DFL, None):
            self._oldSIGTERMHandler = handler

    def _SIGTERMHandler(self, signal, frame):
        print "handling SIGTERM"
        self.debug("handling SIGTERM")
        reactor.killed = True
        self.debug("_SIGTERMHandler: shutting down jobheaven")
        d = self.job_heaven.shutdown()

        if self._oldSIGTERMHandler:
            if d:
                self.debug("chaining Twisted handler")
                d.addCallback(lambda result: self._oldSIGTERMHandler(signal, frame))
            else:
                self.debug("calling Twisted handler")
                self._oldSIGTERMHandler(signal, frame)

        self.debug("_SIGTERMHandler: done")

    def deferredStartCreate(self, avatarId):
        """
        Create and register a deferred for starting up the given component.
        This deferred will be fired when the JobAvatar has instructed the
        job to start the component.
        """
        self.debug('creating start deferred for %s' % avatarId)
        if avatarId in self._startDeferreds.keys():
            self.warning('Already a start deferred registered for %s' %
                avatarId)
            return None

        d = defer.Deferred()
        self._startDeferreds[avatarId] = d
        return d

    def deferredStartTrigger(self, avatarId):
        """
        Trigger a previously registered deferred for starting up the given
        component.
        """
        self.debug('triggering start deferred for %s' % avatarId)
        if not avatarId in self._startDeferreds.keys():
            self.warning('No deferred start registered for %s' % avatarId)
            return

        d = self._startDeferreds[avatarId]
        del self._startDeferreds[avatarId]
        # return the avatarId the component will use to the original caller
        d.callback(avatarId)
 
    def deferredStartFailed(self, avatarId, failure):
        """
        Notify the caller that a start has failed, and remove the start
        from the list of pending starts.
        """
        self.debug('deferred start failed for %s' % avatarId)
        assert avatarId in self._startDeferreds.keys()

        d = self._startDeferreds[avatarId]
        del self._startDeferreds[avatarId]
        d.errback(failure)
 
class JobDispatcher:
    """
    I am a Realm inside the worker for forked jobs to log in to.
    """
    __implements__ = portal.IRealm
    
    def __init__(self, root):
        """
        @type root: L{flumotion.worker.worker.JobHeaven}
        """
        self.root = root
        
    ### portal.IRealm methods
    # flumotion-worker job processes log in to us.
    # The mind is a RemoteReference which allows the brain to call back into
    # the job.
    # the avatar id is of the form /(parent)/(name) 
    def requestAvatar(self, avatarId, mind, *interfaces):
        if pb.IPerspective in interfaces:
            avatar = self.root.createAvatar(avatarId)
            reactor.callLater(0, avatar.attached, mind)
            return pb.IPerspective, avatar, avatar.logout
        else:
            raise NotImplementedError("no interface")

class Port:
    """
    I am an abstraction of a local TCP port which will be used by GStreamer.
    """
    def __init__(self, number):
        self.number = number
        self.used = False

    def free(self):
        self.used = False

    def use(self):
        self.used = True

    def isFree(self):
        return self.used is False

    def getNumber(self):
        return self.number

    def __repr__(self):
        if self.isFree():
            return '<Port %d (unused)>' % self.getNumber()
        else:
            return '<Port %d (used)>' % self.getNumber()

class JobAvatar(pb.Avatar, log.Loggable):
    """
    I am an avatar for the job living in the worker.
    """
    logCategory = 'job-avatar'

    def __init__(self, heaven, avatarId):
        """
        @type  heaven:   L{flumotion.worker.worker.JobHeaven}
        @type  avatarId: string
        """
        
        self.heaven = heaven
        self.avatarId = avatarId
        self.mind = None
        self.debug("created new JobAvatar")
        
        self.feeds = []
            
    def hasRemoteReference(self):
        """
        Check if the avatar has a remote reference to the peer.

        @rtype: boolean
        """
        return self.mind != None

    def attached(self, mind):
        """
        @param mind: reference to the job's JobMedium on which we can call
        @type mind: L{twisted.spread.pb.RemoteReference}
        
        I am scheduled from the dispatcher's requestAvatar method.
        """
        self.mind = mind
        self.log('Client attached mind %s' % mind)
        host = self.heaven.brain.manager_host
        port = self.heaven.brain.manager_port
        transport = self.heaven.brain.manager_transport
        cb = self.mind.callRemote('initial', host, port, transport)
        cb.addCallback(self._cb_afterInitial)

    def _getFreePort(self):
        for port in self.heaven.ports:
            if port.isFree():
                port.use()
                return port

        # XXX: Raise better error message
        raise AssertionError
    
    def _defaultErrback(self, failure):
        self.warning('unhandled remote error: type %s, message %s' % (
            failure.type, failure.getErrorMessage()))
        
    def _startErrback(self, failure, avatarId, type):
        failure.trap(errors.ComponentStart)
        
        self.warning('could not start component %s of type %s: %r' % (
            avatarId, type, failure.getErrorMessage()))
        self.heaven.brain.deferredStartFailed(avatarId, failure)

    def _cb_afterInitial(self, unused):
        kid = self.heaven.brain.kindergarten.getKid(self.avatarId)
        # we got kid.config through WorkerMedium.remote_start from the manager
        feedNames = kid.config.get('feed', [])
        self.log('_cb_afterInitial(): feedNames %r' % feedNames)

        # This is going to be sent to the component
        feedPorts = {} # feedName -> port number
        # This is saved, so we can unmark the ports when shutting down
        self.feeds = []
        for feedName in feedNames:
            port = self._getFreePort()
            feedPorts[feedName] = port.getNumber()
            self.debug('reserving port %r for feed %s' % (port, feedName))
            self.feeds.append((feedName, port))
            
        self.debug('asking job to start with config %r and feedPorts %r' % (
            kid.config, feedPorts))
        d = self.mind.callRemote('start', kid.avatarId, kid.type,
                                 kid.config, feedPorts)

        # make sure that we trigger the start deferred after this
        d.addCallback(
            lambda result, n: self.heaven.brain.deferredStartTrigger(n),
                kid.avatarId)
        d.addErrback(self._startErrback, kid.avatarId, kid.type)
        d.addErrback(self._defaultErrback)
                                          
    def logout(self):
        self.log('logout called, %s disconnected' % self.avatarId)
        self.mind = None
        for feed, port in self.feeds:
            port.free()
        self.feeds = []
        
    def stop(self):
        """
        returns: a deferred marking completed stop.
        """
        self.debug('stopping %s' % self.avatarId)
        if not self.mind:
            return defer.succeed(None)
        
        return self.mind.callRemote('stop')
        
    def remote_ready(self):
        pass

### this is a different kind of heaven, not IHeaven, for now...
class JobHeaven(pb.Root, log.Loggable):
    """
    I am similar to but not quite the same as a manager-side Heaven.
    I manage avatars inside the worker for job processes forked by the worker.
    """
    logCategory = "job-heaven"
    def __init__(self, brain, feederports):
        self.avatars = {}
        self.brain = brain
        self.feederports = feederports

        # FIXME: use and option from the command line for port range
        # Allocate ports
        self.ports = []
        for port in self.feederports:
            self.ports.append(Port(port))
        
    def createAvatar(self, avatarId):
        avatar = JobAvatar(self, avatarId)
        self.avatars[avatarId] = avatar
        return avatar

    def shutdown(self):
        self.debug('Shutting down JobHeaven')
        dl = defer.DeferredList([x.stop() for x in self.avatars.values()])
        return dl

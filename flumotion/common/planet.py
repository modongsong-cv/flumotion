# -*- Mode: Python; -*-
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
Serializable objects from worker through manager to admin for
planet, flow, job and component.
"""

from twisted.spread import pb
from twisted.internet import defer

from flumotion.twisted import flavors
from flumotion.twisted.compat import implements
from flumotion.common import enum

class ManagerPlanetState(flavors.StateCacheable):
    """
    I represent the state of a planet in the manager.

    I have the following keys:

     - name
     - manager
     - atmosphere:   L{ManagerAtmosphereState}
     - flows (list): list of L{ManagerFlowState}
    """
    # FIXME: why is there a 'parent' key ?
    def __init__(self):
        flavors.StateCacheable.__init__(self)
        self.addKey('name')
        self.addKey('parent')
        self.addKey('manager')
        self.addKey('atmosphere')
        self.addListKey('flows')

        # we always have at least one atmosphere
        self.set('atmosphere', ManagerAtmosphereState())
        self.get('atmosphere').set('parent', self)

    def getComponents(self):
        """
        Return a list of all component states in this planet
        (from atmosphere and all flows).

        @rtype: list of L{ManagerComponentState}
        """
        list = []

        a = self.get('atmosphere')
        if a:
            list.extend(a.get('components'))

        flows = self.get('flows')
        if flows:
            for flow in flows:
                list.extend(flow.get('components'))

        return list


class AdminPlanetState(flavors.StateRemoteCache):
    """
    I represent the state of a planet in an admin client.
    See L{ManagerPlanetState}.
    """
    pass
pb.setUnjellyableForClass(ManagerPlanetState, AdminPlanetState)

class ManagerAtmosphereState(flavors.StateCacheable):
    """
    I represent the state of an atmosphere in the manager.
    The atmosphere contains components that do not participate in a flow,
    but provide services to flow components.

    I have the following keys:

     - name:              string, "atmosphere"
     - parent:            L{ManagerPlanetState}
     - components (list): list of L{ManagerComponentState}
    """
 
    def __init__(self):
        flavors.StateCacheable.__init__(self)
        self.addKey('parent')
        self.addListKey('components')
        self.addKey('name')
        self.set('name', 'atmosphere')

    def empty(self):
        """
        Clear out all component entries.

        @returns: a DeferredList that will fire when all notifications are done.
        """
        list = [self.remove('components', c) for c in self.get('components')]
        return defer.DeferredList(list)

class AdminAtmosphereState(flavors.StateRemoteCache):
    """
    I represent the state of an atmosphere in an admin client.
    See L{ManagerAtmosphereState}.
    """
    pass

pb.setUnjellyableForClass(ManagerAtmosphereState, AdminAtmosphereState)

class ManagerFlowState(flavors.StateCacheable):
    """
    I represent the state of a flow in the manager.

    I have the following keys:

     - name:              string, name of the flow
     - parent:            L{ManagerPlanetState}
     - components (list): list of L{ManagerComponentState}
    """
    def __init__(self, **kwargs):
        """
        ManagerFlowState constructor. Any keyword arguments are
        intepreted as initial key-value pairs to set on the new
        ManagerFlowState.
        """
        flavors.StateCacheable.__init__(self)
        self.addKey('name')
        self.addKey('parent')
        self.addListKey('components')
        for k, v in kwargs.items():
            self.set(k, v)

    def empty(self):
        """
        Clear out all component entries
        """
        # take a copy of the list because we're modifying while running
        components = self.get('components')[:]

        list = [self.remove('components', c) for c in components]
        return defer.DeferredList(list)

class AdminFlowState(flavors.StateRemoteCache):
    """
    I represent the state of a flow in an admin client.
    See L{ManagerFlowState}.
    """
    pass

pb.setUnjellyableForClass(ManagerFlowState, AdminFlowState)

# moods
# FIXME. make epydoc like this
"""
@cvar moods: an enum representing the mood a component can be in.
"""
moods = enum.EnumClass(
    'Moods',
    ('happy', 'hungry', 'waking', 'sleeping', 'lost', 'sad')
)
moods.can_stop = staticmethod(lambda m: m != moods.sleeping and m != moods.lost)
moods.can_start = staticmethod(lambda m: m == moods.sleeping)

_jobStateKeys = ['mood', 'manager-ip', 'pid', 'workerName', 'cpu']
_jobStateListKeys = ['messages', ]

# FIXME: maybe make Atmosphere and Flow subclass from a ComponentGroup class ?
class ManagerComponentState(flavors.StateCacheable):
    """
    I represent the state of a component in the manager.
    I have my own state, and also proxy state from the L{ManagerJobState}
    when the component is actually created in a worker.

    I have the following keys of my own:

     - name:              str, name of the component, unique in the parent
     - parent:            L{ManagerFlowState} or L{ManagerAtmosphereState}
     - type:              str, type of the component
     - moodPending:       int, the mood value the component is being set to
     - workerRequested:   str, name of the worker this component is
                          requested to be started on.
     - config:            dict, the configuration dict for this component

    It also has a special key, 'mood'. This acts as a proxy for the mood
    in the L{WorkerJobState}, when there is a job attached (the job's copy
    is authoritative when it connects), and is controlled independently at 
    other times.

    I proxy the following keys from the serialized L{WorkerJobState}:
      - mood, manager-ip, pid, workerName, cpu
      - messages (list)
    """
 
    def __init__(self):
        flavors.StateCacheable.__init__(self)
        # our additional keys
        self.addKey('name')
        self.addKey('type')
        self.addKey('parent')
        self.addKey('moodPending')
        self.addKey('workerRequested')
        self.addKey('config') # dictionary
        
        # proxied from job state or combined with our state (mood)
        for k in _jobStateKeys:
            self.addKey(k)
        for k in _jobStateListKeys:
            self.addListKey(k)
        self._jobState = None

    def __repr__(self):
        return "<ManagerComponentState %s>" % self._dict['name']

    def setJobState(self, jobState):
        """
        Set the job state I proxy from.

        @type jobState: L{ManagerJobState}
        """
        self._jobState = jobState
        for key in _jobStateKeys:
            # only set non-None values
            v = jobState.get(key)
            if v != None:
                self.set(key, v)
        for key in _jobStateListKeys:
            list = jobState.get(key)
            if list != None:
                for v in list:
                    self.append(key, v)
                
        # only proxy keys we want proxied; eaterNames and feederNames
        # are ignored for example
        proxiedKeys = _jobStateKeys + _jobStateListKeys
        def proxy(attr):
            def event(state, key, value):
                if key in proxiedKeys:
                    getattr(self, attr)(key, value)
            return event

        jobState.addListener(self, proxy('set'), proxy('append'),
                             proxy('remove'))

    def clearJobState(self):
        """
        Remove the job state.
        """
        self._jobState.removeListener(self)
        self._jobState = None

class AdminComponentState(flavors.StateRemoteCache):
    """
    I represent the state of a component in the admin client.
    See L{ManagerComponentState}.
    """
    def __repr__(self):
        return "<AdminComponentState %s>" % self._dict['name']

pb.setUnjellyableForClass(ManagerComponentState, AdminComponentState)

# state of an existing component running in a job process
# exchanged between worker and manager
class WorkerJobState(flavors.StateCacheable):
    """
    I represent the state of a job in the worker, running a component.

    I have the following keys:

     - mood:              int, value of the mood this component is in
     - ip:                string, IP address of the worker
     - pid:               int, PID of the job process
     - workerName:        string, name of the worker I'm running on
     - cpu:               float, CPU usage
     - messages:          list of L{flumotion.common.messages.Message}

    In addition, if I am the state of a FeedComponent, then I also
    have the following keys:
    
     - eaterNames:        list of feedId being eaten by the eaters
     - feederNames:       list of feedId being fed by the feeders

    @todo: change eaterNames and feederNames to eaterFeedIds and ...
    """
    def __init__(self):
        flavors.StateCacheable.__init__(self)
        for k in _jobStateKeys:
            self.addKey(k)
        for k in _jobStateListKeys:
            self.addListKey(k)

class ManagerJobState(flavors.StateRemoteCache):
    """
    I represent the state of a job in the manager.
    See L{WorkerJobState}.
    """
    pass

pb.setUnjellyableForClass(WorkerJobState, ManagerJobState)

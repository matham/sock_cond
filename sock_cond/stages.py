# -*- coding: utf-8 -*-
'''The stages of the experiment.
'''

from functools import partial
import traceback
from time import clock, strftime, sleep
from re import match, compile
from os.path import join, isfile
import csv
from random import randint, shuffle

from moa.stage import MoaStage
from moa.threads import ScheduledEventLoop
from moa.utils import (
    ConfigPropertyList, to_bool, ConfigPropertyDict, to_string_list)
from moa.compat import unicode_type
from moa.device.digital import ButtonChannel
from moa.base import named_moas as moas
from moa.stage.delay import Delay

from kivy.app import App
from kivy.properties import (
    ObjectProperty, ListProperty, ConfigParserProperty, NumericProperty,
    BooleanProperty, StringProperty, OptionProperty, DictProperty)
from kivy.clock import Clock
from kivy.factory import Factory
from kivy import resources

from sock_cond.devices import (
    FTDIOdors, FTDIOdorsSim, FTDIPortSim, FTDIPort, RTVChanSim, RTVChan,
    FFPyWriterDevice)
from cplcom import exp_config_name, device_config_name
from cplcom.device.barst_server import Server
from cplcom.device.ftdi import FTDIDevChannel
from cplcom.graphics import FFImage

odor_name_pat = compile('p[0-9]+')


def verify_valve_name(val):
    if not match(odor_name_pat, val):
        raise Exception('{} does not match the valve name pattern'.format(val))
    return unicode_type(val)


class RootStage(MoaStage):
    '''The root stage of the experiment. This stage contains all the other
    experiment stages.
    '''

    def on_finished(self, *largs, **kwargs):
        '''Executed after the root stage and all sub-stages finished. It stops
        all the devices.
        '''
        if self.finished:
            App.get_running_app().app_state = 'clear'
            moas.barst.stop_devices()
            fd = moas.verify._fd
            if fd is not None:
                fd.close()
                moas.verify._fd = None


class InitBarstStage(MoaStage, ScheduledEventLoop):
    '''The stage that creates and initializes all the Barst devices (or
    simulation devices if :attr:`ExperimentApp.simulate`).
    '''

    # if a device is currently being initialized by the secondary thread.
    _finished_init = False
    # if while a device is initialized, stage should stop when finished.
    _should_stop = None

    server = ObjectProperty(None, allownone=True)
    '''The :class:`Server` instance. When :attr:`simulate`, this is None. '''

    ftdi_chan = ObjectProperty(None, allownone=True)
    '''The :class:`FTDIDevChannel` instance. When :attr:`simulate`, this is
    None.
    '''

    odor_dev = ObjectProperty(None, allownone=True)
    '''The :class:`FTDIOdors` instance, or :class:`FTDIOdorsSim` instance when
    :attr:`simulate`.
    '''

    ftdi_pin_dev = ObjectProperty(None, allownone=True)

    players = ListProperty([])

    writers = ListProperty([])

    exp_writers = None

    base_pts = 0

    displays = ListProperty([])

    next_animal_dev = ObjectProperty(None, allownone=True)

    ports = ConfigPropertyList(
        0, 'Video', 'ports', exp_config_name, val_type=int, autofill=False)

    port_names = ConfigPropertyList(
        '', 'Video', 'port_names', exp_config_name, val_type=unicode_type)

    record = ConfigPropertyList(
        False, 'Video', 'record', exp_config_name, val_type=to_bool)

    num_boards = ConfigPropertyList(
        1, 'FTDI_odor', 'num_boards', device_config_name, val_type=int)

    exception_callback = None
    '''The partial function that has been scheduled to be called by the kivy
    thread when an exception occurs. This function must be unscheduled when
    stopping, in case there are waiting to be called after it already has been
    stopped.
    '''

    def __init__(self, **kw):
        super(InitBarstStage, self).__init__(**kw)
        self.exclude_attrs = ['finished']

    def clear(self, *largs, **kwargs):
        self._finished_init = False
        self._should_stop = None
        return super(InitBarstStage, self).clear(*largs, **kwargs)

    def unpause(self, *largs, **kwargs):
        # if simulating, we cannot be in pause state
        if super(InitBarstStage, self).unpause(*largs, **kwargs):
            if self._finished_init:
                # when unpausing, just continue where we were
                self.finish_start_devices()
            return True
        return False

    def stop(self, *largs, **kwargs):
        if self.started and not self._finished_init and not self.finished:
            self._should_stop = largs, kwargs
            return False
        return super(InitBarstStage, self).stop(*largs, **kwargs)

    def service_input_image(self, idx, frame, pts):
        writer = self.writers[idx]
        if writer is not None:
            base_pts = self.base_pts
            if base_pts is None:
                base_pts = self.base_pts = pts
            writer.add_frame(frame, pts - base_pts)
        display = self.displays[idx]
        if display is not None:
            display.display(frame)

    def step_stage(self, *largs, **kwargs):
        if not super(InitBarstStage, self).step_stage(*largs, **kwargs):
            return False

        # if we simulate, create them and step immediately
        try:
            if App.get_running_app().simulate:
                self.create_devices()
                self.step_stage()
            else:
                self.create_devices(sim=False)
                self.request_callback(
                    'start_devices', callback=self.finish_start_devices)
        except Exception as e:
            App.get_running_app().device_exception(e)

        return True

    def create_devices(self, sim=True):
        '''Creates simulated versions of the barst devices.
        '''
        if sim:
            pincls = FTDIPortSim
            odorcls = FTDIOdorsSim
        else:
            pincls = FTDIPort
            odorcls = FTDIOdors
        app = App.get_running_app()
        ids = app.simulation_devices.ids

        self.next_animal_dev = ButtonChannel(
            button=app.next_animal_btn.__self__, name='next_animal')

        dev_cls = [Factory.get('ToggleDevice'), Factory.get('DarkDevice')]
        odor_btns = ids.odors
        odor_btns.clear_widgets()
        for i in range(self.num_boards[0] * 8):
            odor_btns.add_widget(dev_cls[i % 2](text='p{}'.format(i)))
        odors = self.odor_dev = odorcls(
            name='odors', odor_btns=odor_btns.children,
            N=self.num_boards[0] * 8)

        pin = self.ftdi_pin_dev = pincls(
            name='pin_dev', shocker_btn=ids.shocker.__self__)
        if not sim:
            server = self.server = Server()
            server.create_device()
            ftdi = self.ftdi_chan = FTDIDevChannel()
            ftdi.create_device([odors, pin], server)

        players = []
        cam_btns = ids.cams
        names = self.port_names
        N = len(self.ports)
        cam_btns.clear_widgets()
        for i in range(N):
            cam_btns.add_widget(dev_cls[i % 2](text=names[i]))
        for i, p in enumerate(self.ports):
            port = 'player{}'.format(p)
            if sim:
                player = RTVChanSim(
                    button=cam_btns.children[N - 1 - i].__self__, name=port,
                    idx=i, callback=partial(self.service_input_image, i))
            else:
                player = RTVChan(
                    button=cam_btns.children[N - 1 - i].__self__, name=port,
                    idx=i, callback=partial(self.service_input_image, i),
                    port=p)
            if not sim:
                player.create_device(server)
            players.append(player)
        self.players = players
        self.writers = [None, ] * len(players)
        displays = app.displays
        displays.clear_widgets()
        self.displays = [FFImage() for _ in range(len(players))]
        for display in self.displays:
            displays.add_widget(display)

        if sim:
            self.odor_dev.activate(self)
            self.ftdi_pin_dev.activate(self)
            for player in self.players:
                player.activate(self)

    def start_devices(self):
        for dev in [self.server, self.ftdi_chan] + self.players:
            dev.start_channel()

    def finish_start_devices(self, *largs):
        self._finished_init = True
        should_stop = self._should_stop
        if should_stop is not None:
            super(InitBarstStage, self).stop(*should_stop[0], **should_stop[1])
            return
        if self.paused:
            return

        self.odor_dev.activate(self)
        self.ftdi_pin_dev.activate(self)
        for player in self.players:
            player.activate(self)
        self.step_stage()

    def handle_exception(self, exception, event=None):
        '''The overwritten method called by the devices when they encounter
        an exception.
        '''
        callback = self.exception_callback = partial(
            App.get_running_app().device_exception, traceback.format_exc(),
            event)
        Clock.schedule_once(callback)

    def stop_devices(self):
        odor_dev = self.odor_dev
        pin_dev = self.ftdi_pin_dev
        players = self.players
        ftdi_chan = self.ftdi_chan
        server = self.server

        unschedule = Clock.unschedule
        for dev in [odor_dev, pin_dev] + players:
            if dev is not None:
                dev.deactivate(self)
        for writer in self.writers:
            if writer is not None:
                writer.add_frame()
        self.writers = [None, ] * len(self.players)

        unschedule(self.exception_callback)
        self.clear_events()
        self.stop_thread(join=True)
        if App.get_running_app().simulate:
            return

        for dev in [odor_dev, pin_dev, ftdi_chan] + players + [server]:
            if dev is not None:
                dev.stop_device()

        def clear_app(*l):
            App.get_running_app().app_state = 'clear'
        self.start_thread()
        self.request_callback(
            'stop_devices_internal', callback=clear_app, cls_method=True)

    def stop_devices_internal(self):
        '''Called from :class:`InitBarstStage` internal thread. It stops
        and clears the states of all the devices.
        '''
        for dev in [self.odor_dev, self.ftdi_pin_dev, self.ftdi_chan] + \
            self.players + [self.server]:
            try:
                if dev is not None:
                    dev.stop_channel()
            except:
                pass
        self.stop_thread()

    def create_writers(self, filename, num_trials):
        players = self.players
        names = self.port_names
        record = self.record
        btn = App.get_running_app().next_animal_btn
        filedata = {
            'day': btn.day, 'group': btn.group, 'animal': btn.animal_id,
            'cycle': btn.cycle, 'trial': '', 'cam': ''}

        try:
            writers = []
            for trial in range(num_trials):
                filedata['trial'] = trial
                trial_writers = []
                for i, player in enumerate(players):
                    if not record[i]:
                        trial_writers.append(None)
                        continue
                    filedata['cam'] = names[i]
                    while player.size is None or player.rate is None:
                        sleep(0.005)
                    writer = FFPyWriterDevice(
                        filename.format(**filedata), player.size, player.rate,
                        player.output_img_fmt)
                    trial_writers.append(writer)
                writers.append(trial_writers)
            self.exp_writers = writers
        except Exception as e:
            self.handle_exception(e)

    def set_trial_writers(self, trial):
        self.writers = self.exp_writers[trial]
        self.base_pts = None

    def reset_trial_writers(self):
        for writer in self.writers:
            if writer is not None:
                writer.add_frame()
        self.writers = [None] * len(self.players)


class VerifyConfigStage(MoaStage):
    '''Stage that is run before the first block of each animal.

    The stage verifies that all the experimental parameters are correct and
    computes all the values, e.g. odors needed for the trials.

    If the values are incorrect, it calls
    :meth:`ExperimentApp.device_exception` with the exception.
    '''

    def __init__(self, **kw):
        super(VerifyConfigStage, self).__init__(**kw)
        self.exclude_attrs = ['finished']

    def step_stage(self, *largs, **kwargs):
        if not super(VerifyConfigStage, self).step_stage(*largs, **kwargs):
            return False

        try:
            self.read_odors()
            app = App.get_running_app()
            ch = app.simulation_devices.ids.odors.children
            valve = ch[len(ch) - 1 - int(self.NO_valve[1:])]
            valve.background_down = 'dark-blue-led-on-th.png'
            valve.background_normal = 'dark-blue-led-off-th.png'
            for p in [
                valve for valves in moas.rand_valves.rand_valves for
                    valve in valves]:
                valve = ch[len(ch) - 1 - int(p[1:])]
                valve.background_down = 'brown-led-on-th.png'
                valve.background_normal = 'brown-led-off-th.png'
            N = len(ch)
            for i, name in enumerate(self.odor_names):
                ch[N - 1 - i].text = name
            clss = self.exp_classes
            for cls in [v for vals in self.animal_cls.values() for v in vals]:
                if cls not in clss:
                    raise Exception('Protocol {} not recognized'.format(cls))
            for player in moas.barst.players:
                player.set_state(True)
            timer = app.timer
            iti_max = max(self.iti_max.values())
            timer.range = self.prehab + self.pre_record + self.trial_duration \
                + self.post_record + iti_max + self.posthab
            elems = (
                (0, 'Init'), (0, 'Ready'), (self.prehab, 'Pre-hab'),
                (self.pre_record, 'Pre'),
                (self.trial_duration, 'Trial'),
                (self.post_record, 'Post'),
                (iti_max, 'ITI'), (self.posthab, 'Post-hab'),
                (0, 'Done'))
            t = [sum([e[0] for e in elems[:i + 1]]) for i in range(len(elems))]
            txt = [e[1] for e in elems]
            timer.update_slices(t, txt)
        except Exception as e:
            App.get_running_app().device_exception(e)
            return
        self.step_stage()
        return True

    def read_odors(self):
        N = 8 * moas.barst.odor_dev.num_boards[0]
        odor_name = ['p{}'.format(i) for i in range(N)]

        # now read the odor list
        odor_path = resources.resource_find(self.odor_path)
        with open(odor_path, 'rb') as fh:
            for row in csv.reader(fh):
                row = [elem.strip() for elem in row]
                if not row:
                    continue
                i, name = row[:2]
                i = int(i)
                if i >= N:
                    raise Exception('Odor {} is out of bounds: {}'.
                                    format(i, row))
                odor_name[i] = name
        self.odor_names = odor_name

    def start_trials(self):
        try:
            fname = strftime(
                self.log_filename.format(**{'animal': self.animal_id}))
            filename = self._filename

            if filename != fname:
                fd = self._fd
                if fd is not None:
                    fd.close()
                    self._fd = None
                if not fname:
                    return
                fd = self._fd = open(fname, 'a')
                fd.write('Date,RatID,Trial,Time,Odor?,Shock?\n')
                self._filename = fname
        except Exception as e:
            App.get_running_app().device_exception(e)
            return
        self.odor_trial_count = 0
        self.shock_trial_count = 0

    def pre_trial(self):
        self.trial_log['shock'] = self.trial_log['odor'] = False
        App.get_running_app().timer.slices[4].text = 'Trial ({})'.format(moas.trial.count)

        cls = self.curr_animal_cls
        if cls == 'NoOdor':
            return
        elif cls == 'PsdTrain':
            self.trial_log['odor'] = has_odor = ((
                bool(randint(0, 1)) or
                self.shock_trial_count == self.num_shock_trials) and
                self.odor_trial_count <
                self.num_trials['PsdTrain'] - self.num_shock_trials)
            if has_odor:
                self.odor_trial_count += 1
            else:
                self.shock_trial_count += 1
                self.trial_log['shock'] = True
        elif cls == 'StdTrain':
            self.trial_log['shock'] = self.trial_log['odor'] = True
        elif cls == 'OdorOnly':
            self.trial_log['odor'] = True

    def set_odor(self, state):
        if not self.trial_log['odor']:
            return
        dev = moas.barst.odor_dev
        if state:
            dev.set_state(high=[self.odor_valve, self.NO_valve])
        else:
            dev.set_state(low=[self.odor_valve, self.NO_valve])

    def set_shock(self, state):
        if not self.trial_log['shock']:
            return
        dev = moas.barst.ftdi_pin_dev
        if state:
            dev.set_state(high=['shocker'])
        else:
            dev.set_state(low=['shocker'])

    def post_trial(self):
        fd = self._fd
        if fd is None:
            return
        val = '{},{},{trial},{ts},{odor},{shock}'.format(
            strftime('%m/%d/%Y %I:%M:%S %p'), self.animal_id, **self.trial_log)
        fd.write(val)
        fd.write('\n')

    num_trials = ConfigPropertyDict(
        {'StdTrain': 10, 'PsdTrain': 20, 'OdorOnly': 10, 'NoOdor': 10},
        'Trial', 'num_trials', exp_config_name, val_type=int,
        key_type=unicode_type)

    num_shock_trials = ConfigParserProperty(
        1, 'Trial', 'num_shock_trials', exp_config_name, val_type=int)

    odor_valve = ConfigParserProperty(
        'p1', 'Odor', 'odor_valve', exp_config_name,
        val_type=verify_valve_name)

    NO_valve = ConfigParserProperty(
        'p0', 'Odor', 'NO_valve', exp_config_name, val_type=verify_valve_name)

    odor_path = ConfigParserProperty(
        u'odor_list.txt', 'Odor', 'Odor_list_path', exp_config_name,
        val_type=unicode_type)

    odor_names = ListProperty([])

    pre_record = ConfigParserProperty(
        3, 'Video', 'pre_record', exp_config_name, val_type=float)

    post_record = ConfigParserProperty(
        3, 'Video', 'post_record', exp_config_name, val_type=float)

    trial_duration = ConfigParserProperty(
        3, 'Trial', 'trial_duration', exp_config_name, val_type=float)

    shock_duration = ConfigParserProperty(
        1, 'Trial', 'shock_duration', exp_config_name, val_type=float)

    iti_min = ConfigPropertyDict(
        {'StdTrain': 50, 'PsdTrain': 106, 'OdorOnly': 50, 'NoOdor': 50},
        'Trial', 'iti_min', exp_config_name, val_type=float,
        key_type=unicode_type)

    iti_max = ConfigPropertyDict(
        {'StdTrain': 120, 'PsdTrain': 136, 'OdorOnly': 110, 'NoOdor': 110},
        'Trial', 'iti_max', exp_config_name, val_type=float,
        key_type=unicode_type)

    prehab = ConfigParserProperty(
        10, 'Trial', 'prehab', exp_config_name, val_type=float)

    posthab = ConfigParserProperty(
        10, 'Trial', 'posthab', exp_config_name, val_type=float)

    enforce_match = ConfigParserProperty(
        True, 'Experiment', 'enforce_match', exp_config_name, val_type=to_bool)

    animal_cls = ConfigPropertyDict(
        {10: 'StdTrain'}, 'Animal', 'animal_cls', exp_config_name,
        val_type=partial(to_string_list, str), key_type=int)

    days = ConfigPropertyList(
        'hab', 'Animal', 'days', exp_config_name, val_type=str)

    cycles = ConfigPropertyList(
        0, 'Animal', 'cycles', exp_config_name, val_type=int)

    groups = ConfigPropertyList(
        '', 'Animal', 'groups', exp_config_name, val_type=unicode_type)

    video_filename = ConfigParserProperty(
        'RatO1D{day}G{group}R{animal}C{cycle}Trial{trial}Cam{cam}.avi',
        'Video', 'video_filename', exp_config_name, val_type=unicode_type)

    log_filename = ConfigParserProperty('', 'Experiment', 'log_filename',
                                        exp_config_name, val_type=unicode_type)

    trial_log = {'trial': 0, 'odor': False, 'shock': False, 'ts': 0}

    exp_classes = ['StdTrain', 'PsdTrain', 'OdorOnly', 'NoOdor']

    max_t = NumericProperty(0)

    start_t = NumericProperty(0)

    offset_t = NumericProperty(0)

    curr_animal_cls = StringProperty(exp_classes[0])

    odor_trial_count = 0
    shock_trial_count = 0
    _filename = ''
    _fd = None


class RandValves(Delay):

    def __init__(self, **kwargs):
        super(RandValves, self).__init__(**kwargs)
        self.high = []
        self.low = [list(set(valves)) for valves in self.rand_valves]
        self.delay_type = 'random'
        self.max = self.valve_rand_max
        self.min = self.valve_rand_min

    def step_stage(self, *largs, **kwargs):
        if not super(RandValves, self).step_stage(*largs, **kwargs):
            return False

        h = self.high
        l = self.low
        shuffle(h)
        shuffle(l)
        hnew = l[:randint(0, len(l))]
        lnew = h[:randint(0, len(h))]
        self.low = lnew + l[len(hnew):]
        self.high = hnew + h[len(lnew):]

        moas.barst.odor_dev.set_state(
            low=[l for lows in self.low for l in lows],
            high=[h for highs in self.high for h in highs])
        return True

    high = []
    low = []

    rand_valves = ConfigPropertyList(
        'p0', 'Odor', 'rand_valves', exp_config_name,
        val_type=verify_valve_name, inner_list=True)

    valve_rand_min = ConfigParserProperty(
        .4, 'Odor', 'valve_rand_min', exp_config_name, val_type=float)

    valve_rand_max = ConfigParserProperty(
        .8, 'Odor', 'valve_rand_max', exp_config_name, val_type=float)

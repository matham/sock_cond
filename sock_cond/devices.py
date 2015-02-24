''' Devices used in the experiment.
'''


__all__ = ('DeviceStageInterface', 'Server', 'FTDIDevChannel', 'FTDIOdorsBase',
           'FTDIOdorsSim', 'FTDIOdors', 'DAQInDeviceBase', 'DAQInDeviceSim',
           'DAQInDevice', 'DAQOutDeviceBase', 'DAQOutDeviceSim',
           'DAQOutDevice', 'MassFlowControllerBase', 'MassFlowControllerSim',
           'MFCSafe', 'MassFlowController', 'FFpyPlayer')

from threading import Thread
try:
    from Queue import Queue
except:
    from queue import Queue

from ffpyplayer.writer import MediaWriter

from moa.compat import unicode_type
from moa.base import MoaBase
from moa.device.digital import ButtonPort
from moa.utils import ConfigPropertyList
from moa.logger import Logger

from cplcom.device.ftdi import FTDISerializerDevice, FTDIPinDevice
from cplcom.device.rtv import RTVChan as MoaRTVChan
from cplcom.device.ffplayer import FFPyPlayerDevice

from kivy.properties import (
    ConfigParserProperty, BooleanProperty, ListProperty, ObjectProperty,
    NumericProperty, StringProperty)
from kivy.resources import resource_find

from cplcom import device_config_name, exp_config_name
from cplcom.device import DeviceStageInterface


class FTDIOdorsBase(object):
    '''Base class for the FTDI odor devices.
    '''

    def __init__(self, odor_btns=None, N=8, **kwargs):
        Nb = len(odor_btns)
        for i in range(N):
            self.create_property('p{}'.format(i), value=False, allownone=True)
        attr_map = {
            'p{}'.format(i): odor_btns[Nb - i - 1].__self__ for i in range(Nb)}
        super(FTDIOdorsBase, self).__init__(
            attr_map=attr_map, direction='o', **kwargs)


class FTDIOdorsSim(FTDIOdorsBase, ButtonPort):
    '''Device used when simulating the odor devices.
    '''
    pass


class FTDIOdors(FTDIOdorsBase, FTDISerializerDevice):
    '''Device used when using the barst ftdi odor devices.
    '''

    def __init__(self, N=8, **kwargs):
        dev_map = {'p{}'.format(i): i for i in range(N)}
        super(FTDIOdors, self).__init__(dev_map=dev_map, N=N, **kwargs)


class FTDIPortBase(object):

    def __init__(self, shocker_btn=None, **kwargs):
        attr_map = {'shocker': shocker_btn}
        super(FTDIPortBase, self).__init__(
            attr_map=attr_map, direction='o', **kwargs)

    shocker = BooleanProperty(False, allownone=True)


class FTDIPortSim(FTDIPortBase, ButtonPort):
    '''Device used when simulating the odor devices.
    '''
    pass


class FTDIPort(FTDIPortBase, FTDIPinDevice):
    '''Device used when using the barst ftdi odor devices.
    '''

    def __init__(self, **kwargs):
        dev_map = {'shocker': self.shocker_pin}
        super(FTDIPort, self).__init__(dev_map=dev_map, **kwargs)
        self.init_vals['bitmask'] = 1 << self.shocker_pin

    shocker_pin = ConfigParserProperty(
        0, 'FTDI_pin', 'shocker_pin', device_config_name, val_type=int)


def verify_out_fmt(fmt):
    if fmt not in ('rgb24', 'gray'):
        raise Exception('{} is not a valid output format'.format(fmt))
    return fmt


class RTVChanBase(object):

    idx = NumericProperty(0)

    callback = ObjectProperty(None, allownone=True)

    img_fmt = ConfigPropertyList(
        'gray', 'Video', 'img_fmt', exp_config_name,
        val_type=verify_out_fmt)


class RTVChanSim(FFPyPlayerDevice, RTVChanBase):

    def __init__(self, **kwargs):
        super(RTVChanSim, self).__init__(**kwargs)
        names = self.video_name
        if self.idx >= len(names):
            self.filename = resource_find(names[-1])
        else:
            self.filename = resource_find(names[self.idx])
        fmts = self.img_fmt
        if self.idx >= fmts:
            self.output_img_fmt = fmts[-1]
        else:
            self.output_img_fmt = fmts[self.idx]

    video_name = ConfigPropertyList(
        'Wildlife.mp4', 'Video', 'video_name', exp_config_name,
        val_type=unicode_type)


def verify_video_fmt(fmt):
    if fmt not in ('full_NTSC', 'full_PAL', 'CIF_NTSC', 'CIF_PAL', 'QCIF_NTSC',
                   'QCIF_PAL'):
        raise Exception('{} is not a valid RTV video format'.format(fmt))
    return fmt


class RTVChan(MoaRTVChan, RTVChanBase):

    def __init__(self, **kwargs):
        super(RTVChan, self).__init__(**kwargs)
        n = self.idx
        self.output_img_fmt = self.img_fmt[n]
        self.output_video_fmt = self.video_fmt[n]

    video_fmt = ConfigPropertyList(
        'full_NTSC', 'Video', 'video_fmt', exp_config_name,
        val_type=verify_video_fmt)


class FFPyWriterDevice(MoaBase, DeviceStageInterface):

    _frame_queue = None
    _thread = None
    _writer = None

    def __init__(self, filename, size, rate, ifmt, ofmt=None, **kwargs):
        super(FFPyWriterDevice, self).__init__(**kwargs)
        self._frame_queue = Queue()
        if ofmt is None:
            ofmt = 'gray' if ifmt == 'gray' else 'yuv420p'
        self._writer = MediaWriter(
            filename, [{
                'pix_fmt_in': ifmt, 'width_in': size[0], 'height_in': size[1],
                'codec':'rawvideo', 'frame_rate': rate, 'pix_fmt_out': ofmt}])
        self._thread = Thread(
            target=self._record_frames, name='Save frames')
        self._thread.start()

    def add_frame(self, frame=None, pts=0):
        if frame is None:
            self._frame_queue.put('eof', block=False)
        else:
            self._frame_queue.put((frame, pts), block=False)

    def _record_frames(self):
        queue = self._frame_queue
        writer = self._writer

        try:
            while True:
                frame = queue.get(block=True)
                if frame == 'eof':
                    self._writer = None
                    return
                img, pts = frame
                try:
                    writer.write_frame(img, pts, 0)
                except Exception as e:
                    Logger.warning('{}: {}'.format(e, pts))
        except Exception as e:
            self.handle_exception(e)

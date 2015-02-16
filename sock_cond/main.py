'''The main module that starts the experiment.
'''

__all__ = ('ConditioningApp', 'run_app')


from functools import partial
from os.path import join, dirname

from cplcom.app import ExperimentApp, run_app as run_cpl_app

from kivy.properties import ObjectProperty
from kivy.resources import resource_add_path
from kivy.lang import Builder

from sock_cond.graphics import MainView
from sock_cond.stages import RootStage


class ConditioningApp(ExperimentApp):
    '''The app which runs the experiment.
    '''

    displays = ObjectProperty(None)

    timer = ObjectProperty(None)

    def __init__(self, **kwargs):
        super(ConditioningApp, self).__init__(**kwargs)
        self.inspect = True
        resource_add_path(join(dirname(dirname(__file__)), 'data'))
        Builder.load_file(join(dirname(__file__), 'Experiment.kv'))
        Builder.load_file(join(dirname(__file__), 'display.kv'))

    def build(self):
        return super(ConditioningApp, self).build(root=MainView())

    def start_stage(self, restart=False):
        return super(ConditioningApp, self).start_stage(
            RootStage, restart=restart)

run_app = partial(run_cpl_app, ConditioningApp)

if __name__ == '__main__':
    run_app()

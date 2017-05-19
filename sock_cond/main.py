'''The main module that starts the experiment.
'''

__all__ = ('ConditioningApp', 'run_app')


from functools import partial
from os.path import join, dirname

from cplcom.moa.app import ExperimentApp, run_app as run_cpl_app

from kivy.properties import ObjectProperty
from kivy.resources import resource_add_path
from kivy.lang import Builder

import sock_cond.stages


class ConditioningApp(ExperimentApp):
    '''The app which runs the experiment.
    '''

    timer = ObjectProperty(None)

    def __init__(self, **kwargs):
        super(ConditioningApp, self).__init__(**kwargs)
        resource_add_path(join(dirname(dirname(__file__)), 'data'))
        Builder.load_file(join(dirname(__file__), 'Experiment.kv'))
        Builder.load_file(join(dirname(__file__), 'display.kv'))

run_app = partial(run_cpl_app, ConditioningApp)

if __name__ == '__main__':
    run_app()

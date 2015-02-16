from setuptools import setup, find_packages
import sock_cond


setup(
    name='Shock Conditioning',
    version=sock_cond.__version__,
    packages=find_packages(),
    install_requires=['moa', 'pybarst', 'ffpyplayer', 'cplcom'],
    author='Matthew Einhorn',
    author_email='moiein2000@gmail.com',
    url='https://cpl.cornell.edu/',
    license='MIT',
    description='SiWei Conditioning experiment.',
    entry_points={'console_scripts':
                  ['sock_cond=sock_cond.main:run_app']},
    )

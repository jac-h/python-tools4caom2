#/*+
"""
Distutils setup script for tools4caom2
"""
from setuptools import setup, find_packages
from distutils import debug
import sys

if sys.version_info[0] > 2:
    print 'The tools4caom2 package is only compatible with Python version 2.7'
    sys.exit(-1)

# Uncomment the next line for debugging output
# debug.DEBUG=1

setup(name="tools4caom2",
      version='1.1.4',
      description='Python tools to assist ingestions into CAOM-2, ' + \
                  'especially when using fits2caom2',
      author='Russell Redman',
      author_email='russell.o.redman@gmail.com',
      provides=['tools4caom2'],
      requires=['requests (>=2.3.0)', 'vos', 'pyfits'],
      packages=find_packages(exclude=['*.test']),
      test_suite='tools4caom2.test',
      install_requires = ['distribute']
)

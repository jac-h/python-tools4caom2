#!/usr/bin/env python2.7
#/*+
#************************************************************************
#****  C A N A D I A N   A S T R O N O M Y   D A T A   C E N T R E  *****
#*
#* (c) 2013.                  (c) 2013.
#* National Research Council        Conseil national de recherches
#* Ottawa, Canada, K1A 0R6         Ottawa, Canada, K1A 0R6
#* All rights reserved            Tous droits reserves
#*
#* NRC disclaims any warranties,    Le CNRC denie toute garantie
#* expressed, implied, or statu-    enoncee, implicite ou legale,
#* tory, of any kind with respect    de quelque nature que se soit,
#* to the software, including        concernant le logiciel, y com-
#* without limitation any war-        pris sans restriction toute
#* ranty of merchantability or        garantie de valeur marchande
#* fitness for a particular pur-    ou de pertinence pour un usage
#* pose.  NRC shall not be liable    particulier.  Le CNRC ne
#* in any event for any damages,    pourra en aucun cas etre tenu
#* whether direct or indirect,        responsable de tout dommage,
#* special or general, consequen-    direct ou indirect, particul-
#* tial or incidental, arising        ier ou general, accessoire ou
#* from the use of the software.    fortuit, resultant de l'utili-
#*                     sation du logiciel.
#*
#************************************************************************
#*
#*   Script Name:    caom2repo_wrapper.py
#*
#*   Purpose:
#*    A wrapper class for the caom2repo.py command line tool
#*
#+ Usage: 
#+ from tools4caom2.caom2repo_wrapper import Repository
#+ 
#*
#****  C A N A D I A N   A S T R O N O M Y   D A T A   C E N T R E  *****
#************************************************************************
#-*/
__author__ = "Russell O. Redman"


from caom2.caom2_observation_uri import ObservationURI
from contextlib import contextmanager
import errno
import logging
import os.path
import re
import subprocess
import tempfile
import time

from tools4caom2 import __version__

__doc__ = """
The caom2repo_wrapper class immplements methods to collect metadata from the
CAOM-2 repository to get, put and update a CAOM-2 observation, implemented
using the caom2repo.py script provided by caom2repoClient.

Version: """ + __version__.version

class Repository(object):
    """
    Wrapper manager class for the caom2repoClient utility.

    Public Interface:
    There are only three public methods
    1) The constructor
    2) process, a context manager for use in a with statement
    3) remove, to remove an observation from the repository

    The get and put methods are nominally private, and the implementation may
    change to suit the details of the caom2repoClient class.  If get and put
    are called directly, it is the responsibility of the caller to delete
    the temporary file that get may or may not create.

    Notes:
    The caom2repoClient has been implemented initially as a command line
    utility, but is expected to be refactored as an importable module.
    this class shields the user from these changes in implementation.

    This wrapper uses subprocess.check_output() to run each command.  Nonzero
    return values are translated into CalledProcessError exceptions.  Some
    of these are actually success codes, so these exceptions are trapped.  If
    they cannot be handled internally, the error is re-raised as a RuntimeError
    with the output as the message text.  A better approach would be to
    maintain the distinctions amongst the original exceptions, but it is
    anticipated that caom2repoClient will be rewritten to be importable in the
    near future, at which point this entire class may become redundent.

    The caom2repoClient has four methods to get, put, update and remove
    an observation.  The remove action is of no interest here, but the get,
    put and update actions require that state be maintained.  The get action
    returns a disk file if successful, but exits with an error if the
    requested observation does not exist.  Other error conditions are also
    possible and must be trapped.

    If the observation does exist, an observation xml with a unique name
    file will be created in the workdir, which should be used in
    subsequent calls to fits2caom2 as input and output.  The final call
    to push the observation back into the repository must be an update.

    If an observation does not exist in the repository, the observation xml
    file will not be created on disk and subsequent calls to fits2caom2
    must be modified.  The final call to push the observation into the
    repository must be a put.
    """

    def __init__(self, workdir, log, debug=True, backoff=[1.0, 2.0]):
        """
        Create a repository object that remembers the working directory.

        Arguments:
        workdir: path to a directory that will hold temporary files
        log: a logger for progress and error messages
        """
        self.workdir = workdir
        self.log = log
        self.debug = debug
        self.backoff =  backoff

    @contextmanager
    def process( self, uri):
        """
        Context manager to fetch and store  a CAOM-2 observation

        Arguments:
        uri: a CAOM-2 URI identifing an observation that may or may not exist

        Returns:
        <none>

        Exceptions:
        CalledProcessError, returncode == errno.ENOEXEC:
            unable to retrieve observation
        CalledProcessError, returncode == errno.ENOENT:
            no such observation if exists == True
            no such collection if exists == False
        CalledProcessError, returncode == errno.ENOEXEC:
            unable to create observation
        CalledProcessError, returncode == errno.EIO:
            unable to parse observation from file, or,
            unable to read file

        Usage:
        Pseudocode illustrating the intended usage

        repository = Repository('myworkdir', log)
        for observationID in mycollection:
            uri = <make uri from collection and observationID>
            with repository.process(uri) as myfile
                for plane in observation:
                    if os.path.exists(myfile):
                        fits2caom2 --in=myfile --out=myfile <other stuff>
                    else:
                        fits2caom2 --out=myfile <other stuff>
        """
        filepath = ''
        success = False
        try:

            filepath, exists = self.get(uri)
            yield filepath
            self.put(uri, filepath, exists)
            success = True
        finally:
            if (not self.debug and success and 
                filepath and os.path.exists(filepath)):
                # if something goes wrong, retain the xml file for
                # diagnostic purposes
                os.remove(filepath)

    def get( self, uri):
        """
        Get an xml file from the CAOM-2 repository

        Arguments:
        uri: a CAOM-2 URI identifing an observation that may or may not exist

        Returns:
        filepath: path to an xml file, which will not exist if exists == False
        exists: boolean, True if filepath contains the xml for the observation

        Exceptions:
        CalledProcessError, returncode == errno.ENOEXEC:
            unable to retrieve observation
        """
        exists = False
        if isinstance(uri, ObservationURI):
            myuri = uri.uri
        elif isinstance(uri, str):
            myuri = uri
        else:
            myuri = str(uri)
        filepath = tempfile.mktemp(suffix='.xml',
                                   prefix=re.sub(r'[^A-Za-z0-9]+',r'_', myuri),
                                   dir=self.workdir)
        cmd = 'caom2repo.py --debug --retry=5 --get ' + myuri + ' ' + filepath
        self.log.console('PROGRESS: "' + cmd + '"',
                         logging.DEBUG)

        try:
            output = subprocess.check_output(cmd,
                                             stderr=subprocess.STDOUT,
                                             shell=True)
            exists = True
        except subprocess.CalledProcessError as e:
            if (not re.search(r'No such Observation found', e.output)):
                # Recognizing that the observation does not exist is a 
                # success condition.  Otherwise, report the error.
                self.log.console('Command "' + e.cmd +
                                   ' " returned errno.' +
                                   errno.errorcode[e.returncode] +
                                   ' with output "' + e.output + '"',
                                   logging.ERROR)
        except Exception as e:
            retry = False
            self.log.console('caom2repo.py failed with an unexpected '
                             'exception of type ' + type(e) +
                             ' giving reason: ' + str(e),
                             logging.ERROR)
        # kludge to work around a problem with congestion in the caom2repo 
        # service. Back off and try again if get fails.  This can happen 
        # if the rpository web service gets too busy.
#        rep_count = 0
#        retry = True
#        exists = False
#        while retry:
#            try:
#                output = subprocess.check_output(cmd,
#                                                 stderr=subprocess.STDOUT,
#                                                 shell=True)
#                retry = False
#                exists = True
#            except subprocess.CalledProcessError as e:
#                if (re.search(r'No such Observation found', e.output)):
#                    # Recognizing that the observation does not exist is a 
#                    # success, so do not retry
#                    retry = False
#                else:
#                    # get failed and it is not known if the observation exists
#                    if rep_count < len(self.backoff):
#                        self.log.file('caom2repo.py failed: ' + str(e))
#                        self.log.console('retry "' + 
#                                         cmd + '"',
#                                         logging.WARN)
#                        time.sleep(self.backoff[rep_count])
#                        rep_count += 1
#                    else:
#                        retry = False
#                        self.log.console('Command "' + e.cmd +
#                                           ' " returned errno.' +
#                                           errno.errorcode[e.returncode] +
#                                           ' with output "' + e.output + '"',
#                                           logging.ERROR)
#            except Exception as e:
#                retry = False
#                self.log.console('caom2repo.py failed with an unexpected '
#                                 'exception of type ' + type(e) +
#                                 ' giving reason: ' + str(e),
#                                 logging.ERROR)

        # Note that by this point, one of three things will have happened
        # 1) observation does not exist and exista=False
        # 2) observation does exist and exists==True
        # 3) An exception was raised that cannot be handled internally
        # The following statements will only execute in the first two cases.
        if exists:
            self.log.console('Observation ' + myuri + ' was found')
        else:
            self.log.console('Observation ' + myuri + ' was NOT found')
        return (filepath, exists)

    def put(self, uri, filepath, exists):
        """
        Put or update an xml file into the CAOM-2 repository.

        Arguments:
        uri: the CAOM-2 URI of the observation
        filepath: the full path to the CAOM-2 xml file
        exists: if True, use update, else use put

        Exceptions:
        CalledProcessError, returncode == errno.ENOENT:
            no such observation if exists == True
            no such collection if exists == False
        CalledProcessError, returncode == errno.ENOEXEC:
            unable to create observation
        CalledProcessError, returncode == errno.EIO:
            unable to parse observation from file, or,
            unable to read file
        """
        if isinstance(uri, ObservationURI):
            myuri = uri.uri
        elif isinstance(uri, str):
            myuri = uri
        else:
            myuri = str(uri)

        if exists:
            cmd = 'caom2repo.py --debug --retry=5 --update ' + myuri + ' ' + filepath
        else:
            cmd = 'caom2repo.py --debug --retry=5 --put ' + myuri + ' ' + filepath
        self.log.console('PROGRESS: "' + cmd + '"',
                         logging.DEBUG)

        try:
            output = subprocess.check_output(cmd,
                                             stderr=subprocess.STDOUT,
                                             shell=True)
        except subprocess.CalledProcessError as e:
            self.log.console('Command "' + e.cmd +
                               ' " returned errno.' +
                               errno.errorcode[e.returncode] +
                               ' with output "' + e.output + '"',
                               logging.ERROR)
    # kludge to work around a problem that causes caom2repo to report bad 
    # input in valid files.  Back off a second or two and try again with the 
    # same file up to three times
#        rep_count = 0
#        retry = True
#        while retry:
#            try:
#                output = subprocess.check_output(cmd,
#                                                 stderr=subprocess.STDOUT,
#                                                 shell=True)
#                retry = False
#            except subprocess.CalledProcessError as e:
#                # backoff and retry the command
#                if rep_count < len(self.backoff):
#                    self.log.console('retry "' + 
#                                     cmd + '"',
#                                     logging.WARN)
#                    self.log.file('Repcount = ' + str(rep_count) + 
#                                  ': returned errno.' +
#                                  errno.errorcode[e.returncode] +
#                                  ' with output "' + e.output + '"',
#                                  logging.WARN)
#
#                    time.sleep(self.backoff[rep_count])
#                    rep_count += 1
#                else:
#                    retry = False
#                    self.log.console('Command "' + e.cmd +
#                                       ' " returned errno.' +
#                                       errno.errorcode[e.returncode] +
#                                       ' with output "' + e.output + '"',
#                                       logging.ERROR)
#        self.log.console('SUCCESS: "' + cmd + '"')
            

    def remove(self, uri):
        """
        Put or update an xml file into the CAOM-2 repository.

        Arguments:
        uri: the CAOM-2 URI of the observation
        filepath: the full path to the CAOM-2 xml file
        exists: if True, use update, else use put

        Exceptions:
        CalledProcessError, returncode == errno.ENOEXEC:
            unable to remove observation

        Notes:
        This can succeed in two ways: by default if the requested observation
        does not exist, or by successfully removing an observation that does
        exist.
        """
        if isinstance(uri, ObservationURI):
            myuri = uri.uri
        elif isinstance(uri, str):
            myuri = uri
        else:
            myuri = str(uri)

        cmd = 'caom2repo.py --debug --retry=5 --remove ' + myuri
        self.log.console('PROGRESS: "' + cmd + '"',
                         logging.DEBUG)

        try:
            status = subprocess.check_output(cmd,
                                         stderr=subprocess.STDOUT,
                                         shell=True)
        except subprocess.CalledProcessError as e:
            if (e.returncode != errno.ENOENT or 
                not re.search(r'No such Observation found', e.output)):
                # It is no problem if the observation does not exist,
                # but otherwise log the error.
                self.log.console('Command "' + e.cmd +
                                   ' " returned errno.' +
                                   errno.errorcode[e.returncode] +
                                   ' with output "' + e.output + '"',
                                   logging.ERROR)
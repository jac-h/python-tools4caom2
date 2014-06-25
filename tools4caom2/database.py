#!/usr/bin/env python2.7
#/*+
#************************************************************************
#****  C A N A D I A N   A S T R O N O M Y   D A T A   C E N T R E  *****
#*
#* (c) 2012.                  (c) 2012.
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
#*   Script Name:    database.py
#*
#*   Purpose:
#*    Sybase connection for jcmt data in CAOM 2.0
#*
#+ Usage: from jcmt2caom2.database import database
#+
#+ Options:
#*
#*  SVN Fields:
#*    $Revision: 1068 $
#*    $Date: 2012-10-30 16:33:14 -0700 (Tue, 30 Oct 2012) $
#*    $Author: redman $
#*
#****  C A N A D I A N   A S T R O N O M Y   D A T A   C E N T R E  *****
#************************************************************************
#-*/
__author__ = "Russell O. Redman"


import errno
from ConfigParser import SafeConfigParser
from contextlib import contextmanager
import datetime
import logging
import os.path
import re
import subprocess
from threading import Event
from threading import Lock
import traceback

try:
    import Sybase
    sybase_defined = True
except ImportError:
    sybase_defined = False

from tools4caom2 import __version__

__doc__ = """
The database class immplements thread-safe methods methods to interact with 
Sybase databases. 

Version: """ + __version__.version

class database(object):
    """
    Manage connection to Sybase.
    Credentials are read from the operator's .dbrc file or from the userconfig
    dictionary.
    
    The userconfig dictionary can contain definitions for:
    'server': Sybase server
    
    'cred_db': at the CADC, database for which credentials will be found
    or
    'cadc_id': cadc account user_id
    'cadc_key': cadc account password
    
    'read_db': database to read by default ('cred_db' if absent)
    'write_db': database to write by default ('cred_db' if absent)
    
    It is legitimate to omit all of these if no connection is needed.
    
    Usage:
    userconfig['server'] = 'SYBASE'
    userconfig['cred_db'] = 'jcmt'
    mylog = tools4caom2.logger("mylogfile.log")
    
    with database(userconfig, mylog) as db:
        cmd = 'SELECT max(utdate) from jcmtmd.dbo.COMMON'
        max_utdate = db.read(cmd)[0][0]
    
        upodate_cmd = '''UPDATE state = "W"
                         FROM jcmt_discovery
                         WHERE discovery_id = ''' % (IDVALUE,)
        with db.transaction():
            db.write(update_cmd)
    
    This class creates a singleton connection and uses a mutex to control 
    access to the connection, so should be thread-safe.
    """
    
    # class attributes read_mutex and write_mutex
    read_mutex = Lock()
    write_mutex = Lock()
    
    # class attributes read_connection and write_connection
    read_connection = None
    write_connection = None
        
    # class constants for missing data of different types
    NULL = {'query': {'string': '"NULL"',
                      'integer': -9999,
                      'float': -9999.0,
                      'datetime': '"2199-01-01 00:00:00.0"'},
            'value': {'string': 'NULL',
                      'integer': -9999,
                      'float': -9999.0,
                      'datetime': datetime.datetime(2199, 01, 01, 0, 0, 0)}}

    class ConnectionError(Exception):
        """
        Report a connection error.
        """
        def __init__(self, value):
            self.value = value

    def __init__(self, userconfig, log, use=True):
        """
        Create a new connection to the Sybase server
        
        Arguments:
        userconfig: user configuration dictionary
        log: the instance of tools4caom2.logger.logger to use
        
        Exceptions:
        IOError: if call to dbrc_get fails
        IOError: if dbrc_get cannot read credentials for SYBASE jcmtmd.dbo
        IOError: if connection to SYBASE fails

        It is legitimate to customize the pause_queue for each connection.
        """
        self.use = use
        self.server = None
        self.cred_db = None
        self.read_db = None
        self.write_db = None
        self.cadc_id = None
        self.cadc_key = None

        if userconfig.has_section('cadc'):
            if userconfig.has_option('cadc', 'server'):
                self.server = userconfig.get('cadc', 'server')
            
            if userconfig.has_option('cadc', 'cred_db'):
                self.cred_db = userconfig.get('cadc', 'cred_db')
            elif userconfig.has_option('cadc', 'read_db'):
                self.cred_db = userconfig.get('cadc', 'read_db')
            
            if userconfig.has_option('cadc', 'read_db'):
                self.read_db = userconfig.get('cadc', 'read_db')
            elif userconfig.has_option('cadc', 'cred_db'):
                self.read_db = userconfig.get('cadc', 'cred_db')
        
            if userconfig.has_option('cadc', 'write_db'):
                self.read_db = userconfig.get('cadc', 'write_db')
            elif userconfig.has_option('cadc', 'cred_db'):
                self.read_db = userconfig.get('cadc', 'cred_db')
            elif userconfig.has_option('cadc', 'read_db'):
                self.read_db = userconfig.get('cadc', 'read_db')

            if userconfig.has_option('cadc', 'cadc_id'):
                self.cadc_id = userconfig.get('cadc', 'cadc_id')
            if userconfig.has_option('cadc', 'cadc_key'):
                self.cadc_key = userconfig.get('cadc', 'cadc_key')

        self.pause_queue = [1.0, 2.0, 3.0]
        self.log = log

    def get_credentials(self):
        """
        Read the users .dbrc file to get credentials, if dbrc_get is defined
        
        Arguments:
        <None>
        """
        if self.use:
            try:
                output = subprocess.check_output(['which', 'dbrc_get'],
                                                 stderr=subprocess.STDOUT)
                use_config = True
            except:
                use_config = False
            if use_config:
                try:
                    credcmd = ['dbrc_get', self.server,  self.cred_db]
                    credentials = subprocess.check_output(credcmd,
                                                    stderr=subprocess.STDOUT)

                    cred = re.split(r'\s+', credentials)
                    if len(cred) < 2:
                        self.log.console('cred = ' + repr(cred) +
                                         ' should contain username, password',
                                         logging.ERROR)

                    self.cadc_id = cred[0]
                    self.cadc_key = cred[1]
                except subprocess.CalledProcessError as e:
                    self.log.console('errno.' + errno.errorcode(e.returnvalue) +
                                     ': ' + credentials,
                                     logging.ERROR)
        
    def get_read_connection(self):
        """
        Create a singleton read connection if necessary.
        Only called from inside the read() method, this is protected by
        the database.read_mutex of that method.
        
        Arguments:
        <None>
        """
        self.log.file('enter get_read_connection')
        if sybase_defined and self.use:
            if not database.read_connection:
                self.get_credentials()
                self.log.file('have credentials')
                # Check that credentials exist
                if not (self.cadc_id and self.cadc_key):
                    
                    self.log.file('No user credentials, so omit '
                                  'opening connection to database')
                else:
                    database.read_connection = \
                        Sybase.connect(self.server,
                                       self.cadc_id,
                                       self.cadc_key,
                                       database=self.read_db,
                                       auto_commit=1,
                                       datetime='python')
                    if not database.read_connection:
                        self.log.console('Could not connect to ' + 
                                         self.server + ':' +
                                         self.read_db,
                                         logging.ERROR)
        else:
            self.log.file('cannot open a read_connection to a database '
                          'because Sybase is not available')
        self.log.file('leave get_read_connection')

            
    def get_write_connection(self, write_db):
        """
        Create a singleton write connection if necessary
        Only called from inside the write() method, this is protected by
        the database.write_mutex of that method.
        
        Arguments:
        <None>
        """
        if sybase_defined and self.use:
            if not database.write_connection:
                self.get_credentials()
                # Check that credentials exist
                if not (self.cadc_id and self.cadc_key):
                    
                    self.log.file('No user credentials, so omit '
                                  'opening connection to database')
                else:
                    database.write_connection = \
                        Sybase.connect(self.server,
                                       self.cadc_id,
                                       self.cadc_key,
                                       database=self.write_db,
                                       auto_commit=0,
                                       datetime='python')
                    if not database.write_connection:
                        self.log.console('Could not connect to ' + 
                                         self.server + ':' +
                                         self.write_db,
                                         logging.ERROR)
        else:
            self.log.file('Could not open a write_connection to a database '
                           'because Sybase is not available')

    def read(self, query, params={}):
        """
        Run an sql query, multiple times if necessary, using read_connection.
        Only one read can be active at a time, protected by the read_mutex,
        but it can run in parallel with a write transaction.

        Arguments:
        query: a properly formated SQL select query
        params: dictionary of parameters to pass to execute
        """
        self.log.file(query)
        retry = True
        number = 0
        while retry:
            returnList = []
            try:
                with database.read_mutex:
                    self.get_read_connection()
                    cursor = database.read_connection.cursor()
                    cursor.execute(query, params)
                    returnList = cursor.fetchall()
                    cursor.close()
                    retry = False
                    # should be a no-op
                    # database.read_connection.commit()
            except Exception as e:
                # Do not know what kind of error we will get back
                # only the last one will be reported
                if number < len(self.pause_queue):
                    self.log.console('cursor returned error: '
                                     'wait for %.1f seconds and retry' %
                                     (self.pause_queue[number],),
                                     logging.WARN)
                    t = Event()
                    t.wait(self.pause_queue[number])
                    number += 1
                else:
                    retry = False
                    self.log.console(traceback.format_exc())
                    raise
        return returnList

    def write(self, cmd, params={}, result=False):
        """
        Run an sql query, multiple times if necessary, using write_connection.
        Only one write can be active at a time, protected by the write_mutex.
        Write operations should be done inside a transaction.

        Arguments:
        cmd: a properly formated SQL insert or select into command
        params: dictionary of parameters to pass to execute
        
        The query should not return any rows of output.
        """
        self.log.file(cmd)
        returnList = [[]]
        number = 0
        retry = True
        while retry:
            try:
                with database.write_mutex:
                    self.get_write_connection()
                    cursor = database.write_connection.cursor()
                    cursor.execute(cmd, params)
                    if result:
                        returnList = cursor.fetchall()
                    cursor.close()
                    retry = False
            except Exception as e:
                # Do not know what kind of error we will get back
                # only the last one will be reported
                if number < len(self.pause_queue):
                    self.log.console('cursor returned error: '
                                     'wait for %.1f seconds and retry' %
                                     (self.pause_queue[number],),
                                     logging.WARN)
                    t = Event()
                    t.wait(self.pause_queue[number])
                    number += 1
                else:
                    retry = False
                    self.log.console(traceback.format_exc())
                    raise
        return returnList
    
    @contextmanager
    def transaction(self):
        """
        Start a database transaction using the write connection.  Only one
        transaction can be executed at a time, protected using the
        write_mutex, but it can comprise several select, insert and select into
        statements.
        
        Raising any exception other than a ConnectionError will cause the 
        transaction to be rolled back.  A ConnectionError indicates that the
        connection has been dropped.  The transaction should have been
        rolled back automatically.
        
        Otherwise, the transaction will be commited.  
        """
        try:
            self.write('BEGIN TRANSACTION')
            yield
        except database.ConnectionError as e:
            self.log.console('write_connection has failed BEGIN TRANSACTION:'
                             + str(e),
                             logging.ERROR)
        except Exception as e:
            self.write('ROLLBACK')
            self.log.console('The write_connection has been rolled back:'
                             + str(e),
                             logging.ERROR)
        else:
            self.write('COMMIT')
            
    @classmethod
    def close(cls):
        """
        Close the database conenction
        
        Arguments:
        cls        the class that called the method (ignored)
        """
        if database.read_connection:
            database.read_connection.close()
            database.read_connection = None

        if database.write_connection:
            database.write_connection.close()
            database.write_connection = None

@contextmanager
def connection(userconfig, log, use=True):
    """
    Context manager that creates and yields a database object that
    can be used to create read and write connections, then closes the 
    connections automatically on completion.
    
    Arguments:
    server: one of "DEVSYBASE" or "SYBASE"
    database: database to use for credentials
    log: the instance of tools4caom2.logger.logger to use
    """
    try:
        yield database(userconfig, log, use=use)
    finally:
        database.close()

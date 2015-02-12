
import os
import collections
import subprocess

class Environment(collections.Mapping):
    """An immutable dict-like object for holding information about the
    environment that is passed to each Task."""

    def __init__(self, values=None, **kw):
        if values is None:
            values = {}
        values.update(kw)
        self.__values = values

    def __getitem__(self, key):
        return self.__values[key]

    def __iter__(self):
        return iter(self.__values)

    def __len__(self):
        return len(self.__values)

    def clone(self, update=None, **kw):
        """Used to create a new Environment with some values changed."""

        if update is None:
            update = {}

        values = self.__values.copy()
        values.update(update)
        values.update(kw)
        return Environment(values)

class TaskExecutionError(Exception):
    def __init__(self, message, task, original=None):
        Exception.__init__(self, message)

        if original is None:
            original = self

        self.original = original
        self.task = task
        self.env = task.env

class Task(object):
    def __init__(self, env, dependencies=None):
        self.env = env
        if dependencies is None:
            dependencies = []
        self.dependencies = dependencies

    def isReady(self):
        for dependency in self.dependencies:
            if not dependency.isDone():
                return False
        return True
    
    def isDone(self):
        return self.isReady() and self._finished()
    
    def execute(self):
        for dependency in self.dependencies:
            if not dependency.isDone():
                dependency.execute()
        if not self._finished():
            try:
                self._execute()
            except Exception, e:
                raise TaskExecutionError("Task execution failed", self, e)

            #if not self._finished():
            #    raise TaskUnfinishedError("Task executed but isn't marked as finished! Something has gone wrong.", self)

    def _execute(self):
        raise NotImplementedError()

    def _finished(self):
        raise NotImplementedError()

if not hasattr(subprocess, 'check_output'):
    def check_output(*popenargs, **kwargs):
        r"""Run command with arguments and return its output as a byte string.
     
        Backported from Python 2.7 as it's implemented as pure python on stdlib.
     
        >>> check_output(['/usr/bin/python', '--version'])
        Python 2.6.2
        """
        process = subprocess.Popen(stdout=subprocess.PIPE, *popenargs, **kwargs)
        output, unused_err = process.communicate()
        retcode = process.poll()
        if retcode:
            cmd = kwargs.get("args")
            if cmd is None:
                cmd = popenargs[0]
            error = subprocess.CalledProcessError(retcode, cmd)
            error.output = output
            raise error
        return output

    subprocess.check_output = check_output

def execute_cmd(command, capture=False, retry=0):
    try:
      if capture:
          return subprocess.check_output(command, shell=True)

      ret = os.system(command)
      if ret != 0:
          raise Exception("Execution failed: %s" % command)
    except Exception, e:
        if retry > 0:
            return execute_cmd(command, capture, retry - 1)
        else:
            raise

def check_cmd(command):
    return subprocess.call(command, shell=True) == 0

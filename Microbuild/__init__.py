
import collections

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
                depedency.execute()
        if not self.isReady():
            raise Exception("All dependencies executed but still not ready! Something has gone wrong.")
        self._execute()

    def _execute(self):
        raise NotImplementedError()

    def _finished(self):
        raise NotImplementedError()

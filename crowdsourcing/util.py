import itertools
import re

from django.utils.importlib import import_module


def get_function(path):
    """ This used to use import_module, but certain Django-isms such as object
    models appeared to not be available with that approach. """
    parts = path.split(".")
    to_exec = "from %s import %s as got" % (".".join(parts[:-1]), parts[-1])
    try:
        exec(to_exec)
    except ImportError, error:
        raise ImportError(error.msg, to_exec)
    return got


class ChoiceEnum(object):
    def __init__(self, choices):
        if isinstance(choices, basestring):
            choices = choices.split()
        if all([isinstance(choices, (list,tuple)),
                all(isinstance(x, tuple) and len(x) == 2 for x in choices)]):
            values = choices
        else:
            values = zip(itertools.count(1), choices)
        for v, n in values:
            name = re.sub('[- ]', '_', n.upper())
            setattr(self, name, v)
            if isinstance(v, str):
                setattr(self, v.upper(), v)
        self._choices = values

    def __getitem__(self, idx):
        return self._choices[idx]

    def getdisplay(self, key):
        return [v[1] for v in self._choices if v[0] == key][0]

def remove_by_lambda(l, lam):
    """
    Iterate the given list, remove the first entry in the list
    where the given lambda function returns True

    Return True from this function if something was found and removed
    """
    def find(l,func):
        """
        return the index of the first item in list (l) for which
        the function (func) is True
        return -1 if not found
        """
        for i,item in enumerate(l):
            if func(item):
                return i
        return -1
    idx = find(l,lam)
    if idx >= 0:
        l.pop(idx)

    return idx >= 0


""" This is handy for when you don't have sessions or authentication
middleware. """
class DummySession(object):
    session_key = ""

    def __setitem__(self, key, value):
        pass


class DummyUser(object):
    is_staff = False

    def is_authenticated(self):
        return False

    def is_anonymous(self):
        return True


def get_session(request):
    return getattr(request, "session", DummySession())


def get_user(request):
    return getattr(request, "user", DummyUser())

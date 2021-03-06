#
# Copyright 2016 Pixar
#
# Licensed under the Apache License, Version 2.0 (the "Apache License")
# with the following modification; you may not use this file except in
# compliance with the Apache License and the following modification to it:
# Section 6. Trademarks. is deleted and replaced with:
#
# 6. Trademarks. This License does not grant permission to use the trade
#    names, trademarks, service marks, or product names of the Licensor
#    and its affiliates, except as required to comply with Section 4(c) of
#    the License and to reproduce the content of the NOTICE file.
#
# You may obtain a copy of the Apache License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the Apache License with the above modification is
# distributed on an "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
# KIND, either express or implied. See the Apache License for the specific
# language governing permissions and limitations under the Apache License.
#
"""A module for persisting usdview settings
"""

from cPickle import dump, load

class Settings(dict):
    """A small wrapper around the standard Python dictionary.
    
    See help(dict) for initialization arguments This class uses python naming 
    conventions, because it inherits from dict.
    """

    def __init__(self, filename, seq=None, **kwargs):
        self._filename = filename
        if seq:
            dict.__init__(self, seq)
        elif kwargs:
            dict.__init__(self, **kwargs)
    
    def save(self, ignoreErrors=False):
        """Write the settings out to the file at filename
        """
        try:
            f = open(self._filename, "w")
            dump(self, f)
            f.close()
        except:
            if ignoreErrors:
                return False
            raise
        return True
    
    def load(self, ignoreErrors=False):
        """Load the settings from the file at filename
        """
        try:
            f = open(self._filename, "r")
            self.update(load(f))
            f.close()

        except:
            if ignoreErrors:
                return False
            raise
        return True

    def setAndSave(self, **kwargs):
        """Sets keyword arguments as settings and quietly saves
        """
        self.update(kwargs)
        self.save(ignoreErrors=True)


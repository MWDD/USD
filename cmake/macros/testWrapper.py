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
#
# Usage: testWrapper.py <options> <cmd>
#
# Wrapper for test commands which allows us to control the test environment
# and provide additional conditions for passing.
#
# CTest only cares about the eventual return code of the test command it runs
# with 0 being success and anything else indicating failure. This script allows
# us to wrap a test command and provide additional conditions which must be met
# for that test to pass.
#
# Current features include:
# - specifying non-zero return codes
# - comparing output files against a baseline
#
import argparse
import os
import shutil
import subprocess
import sys
import tempfile

def _parseArgs():
    parser = argparse.ArgumentParser(description='USD test wrapper')
    parser.add_argument('--stdout-redirect', type=str,
            help='File to redirect stdout to')
    parser.add_argument('--stderr-redirect', type=str,
            help='File to redirect stderr to')
    parser.add_argument('--diff-compare', nargs='*', 
            help=('Compare output file with a file in the baseline-dir of the '
                  'same name'))
    parser.add_argument('--files-exist', nargs='*',
            help=('Check that a set of files exist.'))
    parser.add_argument('--files-dont-exist', nargs='*',
            help=('Check that a set of files exist.'))
    parser.add_argument('--clean-output-paths', nargs='*',
            help=('Path patterns to remove from the output files being diff\'d.'))
    parser.add_argument('--post-command', nargs='*', 
            help=('Command line action to run after running COMMAND.'))
    parser.add_argument('--pre-command', nargs='*', 
            help=('Command line action to run before running COMMAND.'))
    parser.add_argument('--pre-command-stdout-redirect', type=str, 
            help=('File to redirect stdout to when running PRE_COMMAND.'))
    parser.add_argument('--pre-command-stderr-redirect', type=str, 
            help=('File to redirect stderr to when running PRE_COMMAND.'))
    parser.add_argument('--post-command-stdout-redirect', type=str, 
            help=('File to redirect stdout to when running POST_COMMAND.'))
    parser.add_argument('--post-command-stderr-redirect', type=str, 
            help=('File to redirect stderr to when running POST_COMMAND.'))
    parser.add_argument('--testenv-dir', type=str,
            help='Testenv directory to copy into test run directory')
    parser.add_argument('--baseline-dir',
            help='Baseline directory to use with --diff-compare')
    parser.add_argument('--expected-return-code', type=int, default=0,
            help='Expected return code of this test.')
    parser.add_argument('--env-var', dest='envVars', default=[], type=str, 
            action='append', help='Variable to set in the test environment.')
    parser.add_argument('--verbose', '-v', action='store_true',
            help='Verbose output.')
    parser.add_argument('cmd', metavar='CMD', type=str, nargs='+',
            help='Test command to run')
    return parser.parse_args()

def _resolvePath(baselineDir, fileName):
    # Some test envs are contained within a non-specific subdirectory, if that
    # exists then use it for the baselines
    nonSpecific = os.path.join(baselineDir, 'non-specific')
    if os.path.isdir(nonSpecific):
        baselineDir = nonSpecific

    return os.path.join(baselineDir, fileName)

def _stripPath(f, pathPattern):
    import re

    with open(f, 'r+') as inputFile:
        data = inputFile.read()
        data = re.sub(pathPattern, "", data)
        inputFile.seek(0)
        inputFile.write(data)
        inputFile.truncate()

def _cleanOutput(pathPattern, fileName, verbose):
    _stripPath(fileName, pathPattern)
    if verbose:
        print "stripping path pattern {0} from file {1}".format(pathPattern, 
                                                                fileName)
    return True

def _diff(fileName, baselineDir, verbose):
    # Use the diff program or equivalent, rather than filecmp or similar
    # because it's possible we might want to specify other diff programs
    # in the future.
    import platform
    if platform.system() == 'Windows':
        diff = 'fc.exe'
    else:
        diff = '/usr/bin/diff'
    cmd = [diff, _resolvePath(baselineDir, fileName), fileName]
    if verbose:
        print "diffing with {0}".format(cmd)

    # This will print any diffs to stdout which is a nice side-effect
    return subprocess.call(cmd) == 0

def _copyTree(src, dest):
    ''' Copies the contents of src into dest.'''
    if not os.path.exists(dest):
        os.makedirs(dest)
    for item in os.listdir(src):
        s = os.path.join(src, item)
        d = os.path.join(dest, item)
        if os.path.isdir(s):
            shutil.copytree(s, d)
        else:
            shutil.copy2(s, d) 


# Windows command prompt will not split arguments so we do it instead.
# args.cmd is a list of strings to split so the inner list comprehension
# makes a list of argument lists.  The outer double comprehension flattens
# that into a single list.
def _splitCmd(cmd):
    return [arg for tmp in [arg.split() for arg in cmd] for arg in tmp]

# subprocess.call returns -N if the process raised signal N. Convert this
# to the standard positive error code matching that signal. e.g. if the
# process encounters an SIGABRT signal it will return -6, but we really
# want the exit code 134 as that is what the script would return when run
# from the shell. This is well defined to be 128 + (signal number).
def _convertRetCode(retcode):
    if retcode < 0:
        return 128 + abs(retcode)
    return retcode

def _getRedirects(out_redir, err_redir):
    return (open(out_redir, 'w') if out_redir else None,
            open(err_redir, 'w') if err_redir else None)

def _runCommand(raw_command, stdout_redir, stderr_redir, env,
                expected_return_code):
    cmd = _splitCmd(raw_command)
    fout, ferr = _getRedirects(stdout_redir, stderr_redir)
    try:
        retcode = _convertRetCode(subprocess.call(cmd, shell=False, env=env,
                                  stdout=(fout or sys.stdout), 
                                  stderr=(ferr or sys.stderr)))
    finally:
        if fout:
            fout.close()
        if ferr:
            ferr.close()

    # The return code didn't match our expected error code, even if it
    # returned 0 -- this is a failure.
    if retcode != expected_return_code:
        if args.verbose:
            print sys.stderr, ("Error: return code {0} doesn't match "
                "expected {1} (EXPECTED_RETURN_CODE).".format(retcode, expected_return_code))
        sys.exit(1)       

if __name__ == '__main__':
    args = _parseArgs()

    if args.diff_compare and not args.baseline_dir:
        print sys.stderr, "Error: --baseline-dir must be specified with " \
                "--diff-compare"
        sys.exit(1)

    if args.clean_output_paths and not args.diff_compare:
        print sys.stderr, "Error: --diff-compare must be specified with " \
                "--clean-output-paths"
        sys.exit(1)

    testDir = tempfile.mkdtemp()
    os.chdir(testDir)
    if args.verbose:
        print "chdir: {0}".format(testDir)

    # Copy the contents of the testenv directory into our test run directory so
    # the test has it's own copy that it can reference and possibly modify.
    if args.testenv_dir and os.path.isdir(args.testenv_dir):
        if args.verbose:
            print "copying testenv dir: {0}".format(args.testenv_dir)
        try:
            _copyTree(args.testenv_dir, os.getcwd())
        except Exception as e:
            print sys.stderr, "Error: copying testenv directory: {0}".format(e)
            sys.exit(1)

    # Add any envvars specified with --env-var options into the environment
    env = os.environ.copy()
    for varStr in args.envVars:
        try:
            k, v = varStr.split('=')
            env[k] = v
        except IndexError:
            print sys.stderr, "Error: envvar '{0}' not of the form key=value" \
                    .format(varStr)
            sys.exit(1)

    # Avoid the just-in-time debugger where possible when running tests.
    env['ARCH_AVOID_JIT'] = '1'

    if args.pre_command:
        _runCommand(args.pre_command, args.pre_command_stdout_redirect,
                    args.pre_command_stderr_redirect, env, 0)
        
    _runCommand(args.cmd, args.stdout_redirect, args.stderr_redirect,
                env, args.expected_return_code)

    if args.post_command:
        _runCommand(args.post_command, args.post_command_stdout_redirect,
                    args.post_command_stderr_redirect, env, 0)

    if args.clean_output_paths:
        for path in args.clean_output_paths:
            for diff in args.diff_compare:
                _cleanOutput(path, diff, args.verbose)

    if args.files_exist:        
        for f in args.files_exist:
            if args.verbose:
                print 'checking if {0} exists.'.format(f)
            if not os.path.exists(f):
                print sys.stderr, ('Error: {0} does not exist '
                                   '(FILES_EXIST).'.format(f))
                sys.exit(1)
        
    if args.files_dont_exist:
        for f in args.files_dont_exist:
            if args.verbose:
                print 'checking if {0} does not exist.'.format(f)
            if os.path.exists(f):
                print sys.stderr, ('Error: {0} does exist '
                                   '(FILES_DONT_EXIST).'.format(f))
                sys.exit(1)

    # If desired, diff the provided file(s) (must be generated by the test somehow)
    # with a file of the same name in the baseline directory
    if args.diff_compare:
        for diff in args.diff_compare:
            if not _diff(diff, args.baseline_dir, args.verbose):
                if args.verbose:
                    print sys.stderr, ('Error: diff for {0} failed '
                                       '(DIFF_COMPARE).'.format(diff))
                sys.exit(1)

    sys.exit(0)

"""Unit tests for MercurialClient."""

from __future__ import unicode_literals

import os
import re
import shutil
import tempfile
import time
from hashlib import md5
from random import randint
from textwrap import dedent

from kgb import SpyAgency
from nose import SkipTest
from six.moves import range

from rbtools.clients import RepositoryInfo
from rbtools.clients.errors import CreateCommitError
from rbtools.clients.mercurial import MercurialClient
from rbtools.clients.tests import (FOO, FOO1, FOO2, FOO3, FOO4, FOO5, FOO6,
                                   SCMClientTests)
from rbtools.utils.encoding import force_unicode
from rbtools.utils.filesystem import is_exe_in_path, load_config
from rbtools.utils.process import execute


class MercurialTestBase(SCMClientTests):
    """Base class for all Mercurial unit tests."""

    #: Environment variables to use by default when calling Mercurial.
    hg_env = {}

    def run_hg(self, command, **kwargs):
        """Run a Mercurial command.

        Args:
            command (list of unicode):
                The command and arguments to pass to :program:`hg`.

            **kwargs (dict):
                Additional keyword arguments to pass to
                :py:func:`~rbtools.utils.process.execute`.

        Returns:
            object:
            The result of :py:func:`~rbtools.utils.process.execute`.
        """
        # We're *not* doing `env = env or {}` here because we want the caller
        # to be able to enable reading of user and system-level hgrc
        # configuration.
        env = self.hg_env.copy()

        if not env:
            env = {
                'HGRCPATH': os.devnull,
                'HGPLAIN': '1',
            }

        return execute(['hg'] + command,
                       env,
                       split_lines=False,
                       results_unicode=False,
                       **kwargs)

    def hg_add_file_commit(self, filename, data, msg, branch=None):
        """Add a file to the repository and commit it.

        This can also optionally construct a branch for the commit.

        Args:
            filename (unicode):
                The name of the file to write.

            data (bytes):
                The data to write to the file.

            msg (unicode):
                The commit message.

            branch (unicode, optional):
                The optional branch to create.
        """
        with open(filename, 'wb') as f:
            f.write(data)

        if branch:
            self.run_hg(['branch', branch])

        self.run_hg(['commit', '-A', '-m', msg, filename])


class MercurialClientTests(SpyAgency, MercurialTestBase):
    """Unit tests for MercurialClient."""

    TESTSERVER = 'http://127.0.0.1:8080'
    CLONE_HGRC = dedent("""
        [ui]
        username = test user <user at example.com>

        [paths]
        default = %(hg_dir)s
        cloned = %(clone_dir)s

        [reviewboard]
        url = %(test_server)s

        [diff]
        git = true
    """).rstrip()

    AUTHOR = type(
        str('Author'),
        (object,),
        {
            'fullname': 'name',
            'email': 'email',
        })

    def setUp(self):
        super(MercurialClientTests, self).setUp()

        if not is_exe_in_path('hg'):
            raise SkipTest('hg not found in path')

        self.hg_dir = os.path.join(self.testdata_dir, 'hg-repo')
        self.clone_dir = self.chdir_tmp()
        self.clone_hgrc_path = os.path.join(self.clone_dir, '.hg', 'hgrc')

        self.run_hg(['clone', '--stream', self.hg_dir, self.clone_dir])
        self.client = MercurialClient(options=self.options)

        with open(self.clone_hgrc_path, 'w') as fp:
            fp.write(self.CLONE_HGRC % {
                'hg_dir': self.hg_dir,
                'clone_dir': self.clone_dir,
                'test_server': self.TESTSERVER,
            })

        self.options.parent_branch = None

    def test_get_repository_info(self):
        """Testing MercurialClient.get_repository_info"""
        ri = self.client.get_repository_info()

        self.assertIsInstance(ri, RepositoryInfo)
        self.assertEqual(ri.base_path, '')

        hgpath = ri.path

        if os.path.basename(hgpath) == '.hg':
            hgpath = os.path.dirname(hgpath)

        self.assertEqual(self.hg_dir, hgpath)
        self.assertTrue(ri.supports_parent_diffs)
        self.assertFalse(ri.supports_changesets)

    def test_scan_for_server(self):
        """Testing MercurialClient.scan_for_server"""
        os.rename(self.clone_hgrc_path,
                  os.path.join(self.clone_dir, '._disabled_hgrc'))

        self.client.hgrc = {}
        self.client._load_hgrc()
        ri = self.client.get_repository_info()

        self.assertIsNone(self.client.scan_for_server(ri))

    def test_scan_for_server_when_present_in_hgrc(self):
        """Testing MercurialClient.scan_for_server when present in hgrc"""
        ri = self.client.get_repository_info()

        self.assertEqual(self.client.scan_for_server(ri),
                         self.TESTSERVER)

    def test_scan_for_server_reviewboardrc(self):
        """Testing MercurialClient.scan_for_server when in .reviewboardrc"""
        with self.reviewboardrc({'REVIEWBOARD_URL': self.TESTSERVER}):
            self.client.config = load_config()
            ri = self.client.get_repository_info()

            self.assertEqual(self.client.scan_for_server(ri),
                             self.TESTSERVER)

    def test_diff(self):
        """Testing MercurialClient.diff"""
        self.hg_add_file_commit(filename='foo.txt',
                                data=FOO1,
                                msg='delete and modify stuff')

        revisions = self.client.parse_revision_spec([])
        result = self.client.diff(revisions)

        self.assertIsInstance(result, dict)
        self.assertIn('diff', result)
        self.assertEqual(md5(result['diff']).hexdigest(),
                         '68c2bdccf52a4f0baddd0ac9f2ecb7d2')

    def test_diff_with_multiple(self):
        """Testing MercurialClient.diff with multiple commits"""
        self.hg_add_file_commit(filename='foo.txt',
                                data=FOO1,
                                msg='commit 1')
        self.hg_add_file_commit(filename='foo.txt',
                                data=FOO2,
                                msg='commit 2')
        self.hg_add_file_commit(filename='foo.txt',
                                data=FOO3,
                                msg='commit 3')

        revisions = self.client.parse_revision_spec([])
        result = self.client.diff(revisions)

        self.assertIsInstance(result, dict)
        self.assertIn('diff', result)
        self.assertEqual(md5(result['diff']).hexdigest(),
                         '9c8796936646be5c7349973b0fceacbd')

    def test_diff_with_exclude_patterns(self):
        """Testing MercurialClient.diff with exclude_patterns"""
        self.hg_add_file_commit(filename='foo.txt',
                                data=FOO1,
                                msg='commit 1')
        self.hg_add_file_commit(filename='exclude.txt',
                                data=FOO2,
                                msg='commit 2')

        revisions = self.client.parse_revision_spec([])
        result = self.client.diff(revisions, exclude_patterns=['exclude.txt'])

        self.assertIsInstance(result, dict)
        self.assertIn('diff', result)
        self.assertEqual(md5(result['diff']).hexdigest(),
                         '68c2bdccf52a4f0baddd0ac9f2ecb7d2')

    def test_diff_with_exclude_patterns_no_matches(self):
        """Testing MercurialClient.diff with exclude_patterns and no matched
        files
        """
        self.hg_add_file_commit(filename='foo.txt',
                                data=FOO1,
                                msg='commit 1')
        self.hg_add_file_commit(filename='empty.txt',
                                data=b'',
                                msg='commit 2')

        revisions = self.client.parse_revision_spec([])
        result = self.client.diff(revisions, exclude_patterns=['empty.txt'])

        self.assertIsInstance(revisions, dict)
        self.assertIn('diff', result)
        self.assertEqual(md5(result['diff']).hexdigest(),
                         '68c2bdccf52a4f0baddd0ac9f2ecb7d2')

    def test_diff_with_diverged_branch(self):
        """Testing MercurialClient.diff with diverged branch"""
        self.hg_add_file_commit(filename='foo.txt',
                                data=FOO1,
                                msg='commit 1')

        self.run_hg(['branch', 'diverged'])
        self.hg_add_file_commit(filename='foo.txt',
                                data=FOO2,
                                msg='commit 2')

        revisions = self.client.parse_revision_spec([])
        result = self.client.diff(revisions)

        self.assertIsInstance(result, dict)
        self.assertIn('diff', result)
        self.assertEqual(md5(result['diff']).hexdigest(),
                         '6b12723baab97f346aa938005bc4da4d')

        self.run_hg(['update', '-C', 'default'])

        revisions = self.client.parse_revision_spec([])
        result = self.client.diff(revisions)

        self.assertIsInstance(result, dict)
        self.assertIn('diff', result)
        self.assertEqual(md5(result['diff']).hexdigest(),
                         '68c2bdccf52a4f0baddd0ac9f2ecb7d2')

    def test_diff_with_parent_diff(self):
        """Testing MercurialClient.diff with parent diffs"""
        self.hg_add_file_commit(filename='foo.txt',
                                data=FOO1,
                                msg='commit 1')
        self.hg_add_file_commit(filename='foo.txt',
                                data=FOO2,
                                msg='commit 2')
        self.hg_add_file_commit(filename='foo.txt',
                                data=FOO3,
                                msg='commit 3')

        revisions = self.client.parse_revision_spec(['2', '3'])
        result = self.client.diff(revisions)

        self.assertIsInstance(result, dict)
        self.assertIn('parent_diff', result)
        self.assertEqual(md5(result['diff']).hexdigest(),
                         '7a897f68a9dc034fc1e42fe7a33bb808')
        self.assertEqual(md5(result['parent_diff']).hexdigest(),
                         '5cacbd79800a9145f982dcc0908b6068')

    def test_diff_with_parent_diff_and_diverged_branch(self):
        """Testing MercurialClient.diff with parent diffs and diverged branch
        """
        # This test is very similar to test_diff_with_parent_diff except we
        # throw a branch into the mix.
        self.hg_add_file_commit(filename='foo.txt',
                                data=FOO1,
                                msg='commit 1')
        self.run_hg(['branch', 'diverged'])
        self.hg_add_file_commit(filename='foo.txt',
                                data=FOO2,
                                msg='commit 2')
        self.hg_add_file_commit(filename='foo.txt',
                                data=FOO3,
                                msg='commit 3')

        revisions = self.client.parse_revision_spec(['2', '3'])
        result = self.client.diff(revisions)

        self.assertIn('parent_diff', result)
        self.assertEqual(md5(result['diff']).hexdigest(),
                         '7a897f68a9dc034fc1e42fe7a33bb808')
        self.assertEqual(md5(result['parent_diff']).hexdigest(),
                         '5cacbd79800a9145f982dcc0908b6068')

    def test_diff_with_parent_diff_using_option(self):
        """Testing MercurialClient.diff with parent diffs using --parent"""
        # This test is very similar to test_diff_with_parent_diff except we
        # use the --parent option to post without explicit revisions
        self.hg_add_file_commit(filename='foo.txt',
                                data=FOO1,
                                msg='commit 1')
        self.hg_add_file_commit(filename='foo.txt',
                                data=FOO2,
                                msg='commit 2')
        self.hg_add_file_commit(filename='foo.txt',
                                data=FOO3,
                                msg='commit 3')

        self.options.parent_branch = '2'

        revisions = self.client.parse_revision_spec([])
        result = self.client.diff(revisions)

        self.assertIsInstance(result, dict)
        self.assertIn('parent_diff', result)
        self.assertEqual(md5(result['diff']).hexdigest(),
                         '7a897f68a9dc034fc1e42fe7a33bb808')
        self.assertEqual(md5(result['parent_diff']).hexdigest(),
                         '5cacbd79800a9145f982dcc0908b6068')

    def test_parse_revision_spec_with_no_args(self):
        """Testing MercurialClient.parse_revision_spec with no arguments"""
        base = self._hg_get_tip()
        self.hg_add_file_commit(filename='foo.txt',
                                data=FOO1,
                                msg='commit 1')
        self.hg_add_file_commit(filename='foo.txt',
                                data=FOO2,
                                msg='commit 2')

        tip = self._hg_get_tip()
        revisions = self.client.parse_revision_spec([])

        self.assertIsInstance(revisions, dict)
        self.assertIn('base', revisions)
        self.assertIn('tip', revisions)
        self.assertNotIn('parent_base', revisions)
        self.assertEqual(revisions['base'], base)
        self.assertEqual(revisions['tip'], tip)

    def test_parse_revision_spec_with_one_arg_periods(self):
        """Testing MercurialClient.parse_revision_spec with r1..r2 syntax"""
        base = self._hg_get_tip()
        self.hg_add_file_commit(filename='foo.txt',
                                data=FOO1,
                                msg='commit 1')

        tip = self._hg_get_tip()
        revisions = self.client.parse_revision_spec(['0..1'])

        self.assertIsInstance(revisions, dict)
        self.assertIn('base', revisions)
        self.assertIn('tip', revisions)
        self.assertNotIn('parent_base', revisions)
        self.assertEqual(revisions['base'], base)
        self.assertEqual(revisions['tip'], tip)

    def test_parse_revision_spec_with_one_arg_colons(self):
        """Testing MercurialClient.parse_revision_spec with r1::r2 syntax"""
        base = self._hg_get_tip()
        self.hg_add_file_commit(filename='foo.txt',
                                data=FOO1,
                                msg='commit 1')

        tip = self._hg_get_tip()
        revisions = self.client.parse_revision_spec(['0..1'])

        self.assertIsInstance(revisions, dict)
        self.assertIn('base', revisions)
        self.assertIn('tip', revisions)
        self.assertNotIn('parent_base', revisions)
        self.assertEqual(revisions['base'], base)
        self.assertEqual(revisions['tip'], tip)

    def test_parse_revision_spec_with_one_arg(self):
        """Testing MercurialClient.parse_revision_spec with one revision"""
        base = self._hg_get_tip()
        self.hg_add_file_commit(filename='foo.txt',
                                data=FOO1,
                                msg='commit 1')
        tip = self._hg_get_tip()
        self.hg_add_file_commit(filename='foo.txt',
                                data=FOO2,
                                msg='commit 2')

        revisions = self.client.parse_revision_spec(['1'])

        self.assertIsInstance(revisions, dict)
        self.assertIn('base', revisions)
        self.assertIn('tip', revisions)
        self.assertNotIn('parent_base', revisions)
        self.assertEqual(revisions['base'], base)
        self.assertEqual(revisions['tip'], tip)

    def test_parse_revision_spec_with_two_args(self):
        """Testing MercurialClient.parse_revision_spec with two revisions"""
        base = self._hg_get_tip()
        self.hg_add_file_commit(filename='foo.txt',
                                data=FOO1,
                                msg='commit 1')
        self.hg_add_file_commit(filename='foo.txt',
                                data=FOO2,
                                msg='commit 2')
        tip = self._hg_get_tip()

        revisions = self.client.parse_revision_spec(['0', '2'])

        self.assertIsInstance(revisions, dict)
        self.assertIn('base', revisions)
        self.assertIn('tip', revisions)
        self.assertNotIn('parent_base', revisions)
        self.assertEqual(revisions['base'], base)
        self.assertEqual(revisions['tip'], tip)

    def test_parse_revision_spec_with_parent_base(self):
        """Testing MercurialClient.parse_revision_spec with parent base"""
        start_base = self._hg_get_tip()
        self.hg_add_file_commit(filename='foo.txt',
                                data=FOO1,
                                msg='commit 1')
        commit1 = self._hg_get_tip()
        self.hg_add_file_commit(filename='foo.txt',
                                data=FOO2,
                                msg='commit 2')
        commit2 = self._hg_get_tip()
        self.hg_add_file_commit(filename='foo.txt',
                                data=FOO3,
                                msg='commit 3')
        commit3 = self._hg_get_tip()
        self.hg_add_file_commit(filename='foo.txt',
                                data=FOO4,
                                msg='commit 4')
        commit4 = self._hg_get_tip()
        self.hg_add_file_commit(filename='foo.txt',
                                data=FOO5,
                                msg='commit 5')

        self.assertEqual(
            self.client.parse_revision_spec(['1', '2']),
            {
                'base': commit1,
                'tip': commit2,
                'parent_base': start_base,
            })

        self.assertEqual(
            self.client.parse_revision_spec(['4']),
            {
                'base': commit3,
                'tip': commit4,
                'parent_base': start_base,
                'commit_id': commit4,
            })

        self.assertEqual(
            self.client.parse_revision_spec(['2', '4']),
            {
                'base': commit2,
                'tip': commit4,
                'parent_base': start_base,
            })

    def test_get_commit_message_with_one_commit_in_range(self):
        """Testing MercurialClient.get_commit_message with range containing
        only one commit
        """
        self.options.guess_summary = True
        self.options.guess_description = True

        self.hg_add_file_commit(filename='foo.txt',
                                data=FOO1,
                                msg='commit 1')

        revisions = self.client.parse_revision_spec([])
        commit_message = self.client.get_commit_message(revisions)

        self.assertEqual(commit_message['summary'], 'commit 1')

    def test_get_commit_message_with_commit_range(self):
        """Testing MercurialClient.get_commit_message with commit range"""
        self.options.guess_summary = True
        self.options.guess_description = True

        self.hg_add_file_commit(filename='foo.txt',
                                data=FOO1,
                                msg='commit 1\n\ndesc1')
        self.hg_add_file_commit(filename='foo.txt',
                                data=FOO2,
                                msg='commit 2\n\ndesc2')
        self.hg_add_file_commit(filename='foo.txt',
                                data=FOO3,
                                msg='commit 3\n\ndesc3')

        revisions = self.client.parse_revision_spec([])
        commit_message = self.client.get_commit_message(revisions)

        self.assertEqual(commit_message['summary'], 'commit 1')
        self.assertEqual(commit_message['description'],
                         'desc1\n\ncommit 2\n\ndesc2\n\ncommit 3\n\ndesc3')

    def test_get_commit_message_with_specific_commit(self):
        """Testing MercurialClient.get_commit_message with specific commit"""
        self.options.guess_summary = True
        self.options.guess_description = True

        self.hg_add_file_commit(filename='foo.txt',
                                data=FOO1,
                                msg='commit 1\n\ndesc1')
        self.hg_add_file_commit(filename='foo.txt',
                                data=FOO2,
                                msg='commit 2\n\ndesc2')
        tip = self._hg_get_tip()
        self.hg_add_file_commit(filename='foo.txt',
                                data=FOO3,
                                msg='commit 3\n\ndesc3')

        revisions = self.client.parse_revision_spec([tip])
        commit_message = self.client.get_commit_message(revisions)

        self.assertEqual(commit_message['summary'], 'commit 2')
        self.assertEqual(commit_message['description'], 'desc2')

    def test_create_commit_with_run_editor_true(self):
        """Testing MercurialClient.create_commit with run_editor set to True"""
        self.spy_on(self.client._execute)

        with open('foo.txt', 'w') as fp:
            fp.write('change')

        self.client.create_commit(message='Test commit message.',
                                  author=self.AUTHOR,
                                  run_editor=True,
                                  files=['foo.txt'])

        self.assertTrue(self.client._execute.last_called_with(
            ['hg', 'commit', '-m', 'TEST COMMIT MESSAGE.', '-u',
             'name <email>', 'foo.txt']))

    def test_create_commit_with_run_editor_false(self):
        """Testing MercurialClient.create_commit with run_editor set to False
        """
        self.spy_on(self.client._execute)

        with open('foo.txt', 'w') as fp:
            fp.write('change')

        self.client.create_commit(message='Test commit message.',
                                  author=self.AUTHOR,
                                  run_editor=False,
                                  files=['foo.txt'])

        self.assertTrue(self.client._execute.last_called_with(
            ['hg', 'commit', '-m', 'Test commit message.', '-u',
             'name <email>', 'foo.txt']))

    def test_create_commit_with_all_files_true(self):
        """Testing MercurialClient.create_commit with all_files set to True"""
        self.spy_on(self.client._execute)

        with open('foo.txt', 'w') as fp:
            fp.write('change')

        self.client.create_commit(message='message',
                                  author=self.AUTHOR,
                                  run_editor=False,
                                  files=[],
                                  all_files=True)

        self.assertTrue(self.client._execute.last_called_with(
            ['hg', 'commit', '-m', 'message', '-u', 'name <email>', '-A']))

    def test_create_commit_with_all_files_false(self):
        """Testing MercurialClient.create_commit with all_files set to False"""
        self.spy_on(self.client._execute)

        with open('foo.txt', 'w') as fp:
            fp.write('change')

        self.client.create_commit(message='message',
                                  author=self.AUTHOR,
                                  run_editor=False,
                                  files=['foo.txt'],
                                  all_files=False)

        self.assertTrue(self.client._execute.last_called_with(
            ['hg', 'commit', '-m', 'message', '-u', 'name <email>',
             'foo.txt']))

    def test_create_commit_with_empty_commit_message(self):
        """Testing MercurialClient.create_commit with empty commit message"""
        with open('foo.txt', 'w') as fp:
            fp.write('change')

        message = (
            "A commit message wasn't provided. The patched files are in "
            "your tree but haven't been committed."
        )

        with self.assertRaisesMessage(CreateCommitError, message):
            self.client.create_commit(message='',
                                      author=self.AUTHOR,
                                      run_editor=True,
                                      files=['foo.txt'])

    def test_create_commit_without_author(self):
        """Testing MercurialClient.create_commit without author information"""
        self.spy_on(self.client._execute)

        with open('foo.txt', 'w') as fp:
            fp.write('change')

        self.client.create_commit(message='Test commit message.',
                                  author=None,
                                  run_editor=True,
                                  files=['foo.txt'])

        self.assertTrue(self.client._execute.last_called_with(
            ['hg', 'commit', '-m', 'TEST COMMIT MESSAGE.', 'foo.txt']))

    def _hg_get_tip(self):
        """Return the revision at the tip of the branch.

        Returns:
            unicode:
            The tip revision.
        """
        return force_unicode(self.run_hg(['identify']).split()[0])


class MercurialSubversionClientTests(MercurialTestBase):
    """Unit tests for hgsubversion."""

    TESTSERVER = 'http://127.0.0.1:8080'

    SVNSERVE_MAX_RETRIES = 12

    hg_env = {'FOO': 'BAR'}

    @classmethod
    def setUpClass(cls):
        super(MercurialSubversionClientTests, cls).setUpClass()

        for exe in ('svnadmin', 'svnserve', 'svn'):
            if not is_exe_in_path(exe):
                raise SkipTest('%s is not available on the system. Skipping.'
                               % exe)

        cls._has_hgsubversion = None

        # Create the repository that we'll be populating and later cloning.
        temp_base_path = tempfile.mkdtemp(prefix='rbtools.')
        cls._svn_temp_base_path = temp_base_path

        svn_repo_path = os.path.join(temp_base_path, 'svnrepo')
        execute(['svnadmin', 'create', svn_repo_path])

        # Fill it with content. First, though, we have to clone it.
        svn_checkout_path = os.path.join(temp_base_path, 'checkout.svn')
        execute(['svn', 'checkout', 'file://%s' % svn_repo_path,
                 svn_checkout_path])
        os.chdir(svn_checkout_path)

        execute(['svn', 'propset', 'reviewboard:url', cls.TESTSERVER,
                 svn_checkout_path])
        execute(['svn', 'mkdir', 'trunk', 'branches', 'tags'])
        execute(['svn', 'commit', '-m', 'Initial commit.'])
        os.chdir(os.path.join(svn_checkout_path, 'trunk'))

        for i, data in enumerate([FOO, FOO1, FOO2]):
            cls.svn_add_file_commit(filename='foo.txt',
                                    data=data,
                                    msg='Test commit %s' % i,
                                    add_file=(i == 0))

        # Launch svnserve so Mercurial can pull from it.
        cls._svnserve_pid = None
        svnserve_port = (os.environ.get('SVNSERVE_PORT') or
                         str(randint(30000, 40000)))

        pid_file = os.path.join(temp_base_path, 'svnserve.pid')
        execute(['svnserve', '--single-thread', '--pid-file', pid_file, '-d',
                 '--listen-port', svnserve_port, '-r', temp_base_path])

        for i in range(0, cls.SVNSERVE_MAX_RETRIES):
            try:
                cls._svnserve_pid = int(open(pid_file).read().strip())
            except (IOError, OSError):
                # Wait to see if svnserve has launched yet.
                time.sleep(0.25)

        if not cls._svnserve_pid:
            raise cls.failureException('Unable to launch svnserve on port %s'
                                       % svnserve_port)

        cls.svn_checkout_url = 'svn://127.0.0.1:%s/svnrepo' % svnserve_port

    @classmethod
    def tearDownClass(cls):
        if cls._svnserve_pid:
            os.kill(cls._svnserve_pid, 9)

        shutil.rmtree(cls._svn_temp_base_path, ignore_errors=True)

        super(MercurialSubversionClientTests, cls).tearDownClass()

    def setUp(self):
        print(os.getcwd())
        super(MercurialSubversionClientTests, self).setUp()

        # Make sure hgsubversion is enabled.
        #
        # This will modify the .hgrc in the temp home directory created
        # for these tests.
        #
        # The "hgsubversion =" tells Mercurial to check for hgsubversion
        # in the default PYTHONPATH.
        home_dir = self.get_user_home()

        with open(os.path.join(home_dir, '.hgrc'), 'w') as fp:
            fp.write('[extensions]\n')
            fp.write('hgsubversion =\n')

        if not self.has_hgsubversion:
            raise SkipTest('hgsubversion is not available or cannot be used. '
                           'Skipping.')

        try:
            self.clone_dir = os.path.join(home_dir, 'checkout.hg')
            self.run_hg(['clone', '--stream', self.svn_checkout_url,
                         self.clone_dir])
        except (OSError, IOError) as e:
            self.fail('Unable to clone Subversion repository: %s' % e)

        os.chdir(self.clone_dir)
        self.options.parent_branch = None
        self.client = MercurialClient(options=self.options)

    @property
    def has_hgsubversion(self):
        """Whether hgsubversion is installed and usable."""
        cls = type(self)
        has_hgsubversion = cls._has_hgsubversion

        if has_hgsubversion is None:
            try:
                output = self.run_hg(['svn', '--help'], ignore_errors=True,
                                      extra_ignore_errors=(255))
                has_hgsubversion = \
                    not re.search(b'unknown command [\'"]svn[\'"]',
                                  output, re.I)
            except OSError:
                has_hgsubversion = False

            cls._has_hgsubversion = has_hgsubversion

        return has_hgsubversion

    @classmethod
    def svn_add_file_commit(self, filename, data, msg, add_file=True):
        with open(filename, 'wb') as fp:
            fp.write(data)

        if add_file:
            execute(['svn', 'add', filename], ignore_errors=True)

        execute(['svn', 'commit', '-m', msg])

    def test_get_repository_info(self):
        """Testing MercurialClient.get_repository_info with SVN"""
        ri = self.client.get_repository_info()

        self.assertEqual(self.client._type, 'svn')
        self.assertEqual(ri.base_path, '/trunk')
        self.assertEqual(ri.path, self.svn_checkout_url)

    def test_calculate_repository_info(self):
        """Testing MercurialClient._calculate_hgsubversion_repository_info
        with SVN determines repository and base paths
        """
        repo_info = self.client._calculate_hgsubversion_repository_info(
            'URL: svn+ssh://testuser@svn.example.net/repo/trunk\n'
            'Repository Root: svn+ssh://testuser@svn.example.net/repo\n'
            'Repository UUID: bfddb570-5023-0410-9bc8-bc1659bf7c01\n'
            'Revision: 9999\n'
            'Node Kind: directory\n'
            'Last Changed Author: user\n'
            'Last Changed Rev: 9999\n'
            'Last Changed Date: 2012-09-05 18:04:28 +0000 (Wed, 05 Sep 2012)'
        )

        self.assertEqual(repo_info.path, 'svn+ssh://svn.example.net/repo')
        self.assertEqual(repo_info.base_path, '/trunk')

    def test_scan_for_server_with_reviewboardrc(self):
        """Testing MercurialClient.scan_for_server with SVN and configured
        .reviewboardrc
        """
        with self.reviewboardrc({'REVIEWBOARD_URL': 'https://example.com/'}):
            self.client.config = load_config()
            ri = self.client.get_repository_info()

            self.assertEqual(self.client.scan_for_server(ri),
                             'https://example.com/')

    def test_scan_for_server_with_property(self):
        """Testing MercurialClient.scan_for_server with SVN and reviewboard:url
        property
        """
        ri = self.client.get_repository_info()

        self.assertEqual(self.client.scan_for_server(ri), self.TESTSERVER)

    def test_diff(self):
        """Testing MercurialClient.diff with SVN"""
        self.client.get_repository_info()

        self.hg_add_file_commit(filename='foo.txt',
                                data=FOO4,
                                msg='edit 4')

        revisions = self.client.parse_revision_spec([])
        result = self.client.diff(revisions)

        self.assertIsInstance(result, dict)
        self.assertIn('diff', result)
        self.assertEqual(md5(result['diff']).hexdigest(),
                         '2eb0a5f2149232c43a1745d90949fcd5')
        self.assertIsNone(result['parent_diff'])

    def test_diff_with_multiple_commits(self):
        """Testing MercurialClient.diff with SVN and multiple commits"""
        self.client.get_repository_info()

        self.hg_add_file_commit(filename='foo.txt',
                                data=FOO4,
                                msg='edit 4')
        self.hg_add_file_commit(filename='foo.txt',
                                data=FOO5,
                                msg='edit 5')
        self.hg_add_file_commit(filename='foo.txt',
                                data=FOO6,
                                msg='edit 6')

        revisions = self.client.parse_revision_spec([])
        result = self.client.diff(revisions)

        self.assertIsInstance(result, dict)
        self.assertIn('diff', result)
        self.assertEqual(md5(result['diff']).hexdigest(),
                         '3d007394de3831d61e477cbcfe60ece8')
        self.assertIsNone(result['parent_diff'])

    def test_diff_with_revision(self):
        """Testing MercurialClient.diff with SVN and specific revision"""
        self.client.get_repository_info()

        self.hg_add_file_commit(filename='foo.txt',
                                data=FOO4,
                                msg='edit 4',
                                branch='b')
        self.hg_add_file_commit(filename='foo.txt',
                                data=FOO5,
                                msg='edit 5',
                                branch='b')
        self.hg_add_file_commit(filename='foo.txt',
                                data=FOO6,
                                msg='edit 6',
                                branch='b')
        self.hg_add_file_commit(filename='foo.txt',
                                data=FOO4,
                                msg='edit 7',
                                branch='b')

        revisions = self.client.parse_revision_spec(['3'])
        result = self.client.diff(revisions)

        self.assertIsInstance(result, dict)
        self.assertIn('diff', result)
        self.assertEqual(md5(result['diff']).hexdigest(),
                         '2eb0a5f2149232c43a1745d90949fcd5')
        self.assertIsNone(result['parent_diff'])

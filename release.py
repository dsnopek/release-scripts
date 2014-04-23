
import os
import shutil
import re
import datetime

from Microbuild import Environment, Task, TaskExecutionError, execute_cmd, check_cmd

def update_makefile_revision(make_file, project_name, revision):
    lines = []

    with open(make_file, 'rt') as fd:
        for line in fd.readlines():
            if re.match(r'projects\[%s\]\[download\]\[revision\]' % project_name, line):
                lines.append('projects[%s][download][revision] = %s\n' % (project_name, revision))
            else:
                lines.append(line)

    with open(make_file, 'wt') as fd:
        for line in lines:
            fd.write(line)

def update_makefile_version(make_file, version):
    lines = []

    with open(make_file, 'rt') as fd:
        for line in fd.readlines():
            match = re.match(r'projects\[([a-z_]+)\]\[version\]', line)
            if match:
                lines.append('projects[%s][version] = %s\n' % (match.group(1), version))
            else:
                lines.append(line)

    with open(make_file, 'wt') as fd:
        for line in lines:
            fd.write(line)

def get_latest_commit_message(root):
    os.chdir(root)
    return execute_cmd('git log --pretty=format:%s -n 1 HEAD', capture=True)

class GitCloneTask(Task):
    def _finished(self):
        return os.path.exists(os.path.join(self.env['root'], '.git', 'config'))
    
    def _execute(self):
        repo = "%s@git.drupal.org:project/%s.git" % (self.env['username'], self.env['project_name'])
        execute_cmd("git clone %s %s" % (repo, self.env['root']))

class GitTagTask(Task):
    def _finished(self):
        os.chdir(self.env['root'])
        return check_cmd("git rev-parse %(new_version)s >/dev/null 2>&1" % self.env)

    def _execute(self):
        os.chdir(self.env['root'])
        execute_cmd("git tag %(new_version)s" % self.env)

class GitPushTagTask(Task):
    def _finished(self):
        os.chdir(self.env['root'])
        tags = execute_cmd("git ls-remote --tags origin", capture=True)
        return self.env['new_version'] in tags

    def _execute(self):
        os.chdir(self.env['root'])
        execute_cmd("git push --tags")

class UpdateReleaseNotesTask(Task):
    def _open(self, mode):
        return open(os.path.join(self.env['root'], 'CHANGELOG.txt'), mode)
    
    def _clean_slate(self):
        os.chdir(self.env['root'])

        # Ensure that we are on the right branch and have no left over changes.
        execute_cmd("git checkout %(branch)s >/dev/null 2>&1" % self.env)
        execute_cmd("git reset --hard >/dev/null")
        execute_cmd("git checkout -- . >/dev/null")

    def _finished(self):
        self._clean_slate()

        try:
            # See if the version is mentioned in the first 30 characters
            with self._open('rt') as fd:
                return self.env['new_version'] in fd.read(30)
        except IOError:
            # We assume this means that there is no changelog.
            return True

    def _execute(self):
        self._clean_slate()

        with self._open('rt') as fd:
            changelog = fd.read()

        entry = execute_cmd("%(drush)s rn %(old_version)s %(branch)s --changelog" % self.env, capture=True)
        entry = entry.replace(self.env['branch'], self.env['new_version'])
        entry = entry.replace('%ad', datetime.date.today().strftime('%Y-%m-%d'))
        entry = entry.replace('\n- .\n', '\n- No changes since last release.\n')

        with self._open('wt') as fd:
            fd.write(entry)
            fd.write(changelog)
        
        # Commit this.
        execute_cmd("git commit -a -m 'Updated CHANGELOG.txt'")

# TODO: This isn't safe to do one at a time on the drupal-org.make - we've got
# to do them all at once when restoring the drupal-org.make.
class UpdateMakeFileForModuleRevisionTask(Task):
    pattern = r'^projects\[%(project_name)s\]\[download\]\[revision\] = ([a-f0-9]+)'

    def _open(self, mode):
        return open(os.path.join(self.env['make_file']), mode)

    def _latest_revision(self):
        os.chdir(self.env['root'])
        return execute_cmd('git rev-parse HEAD', capture=True)[0:7]

    def _finished(self):
        with self._open('rt') as fd:
            regex = self.pattern % self.env
            match = re.search(regex, fd.read(), re.MULTILINE)
            return match and match.group(1) == self._latest_revision()

    def _execute(self):
        update_makefile_revision(self.env['make_file'], self.env['project_name'], self._latest_revision())

class CreateReleaseTask(Task):
    # TODO: This should actually create the release on Drupal.org!
    pass

class PanopolyModuleReleaseTask(Task):
    def __init__(self, *args, **kw):
        Task.__init__(self, *args, **kw)

        env = self.env

        self.dependencies.append(GitCloneTask(env))
        self.dependencies.append(UpdateReleaseNotesTask(env))
        self.dependencies.append(GitTagTask(env))
        if self.env['push']:
            self.dependencies.append(GitPushTagTask(env))
        # TODO: Make the actual release on Drupal.org.
        #self.dependencies.append(CreateReleaseTask(self.env))

        # TODO: This isn't a safe Task! It's messing with global state.
        # We don't put 'panopoly_demo' in the drupal-org.make file.
        if self.env['project_name'] != 'panopoly_demo':
            self.dependencies.append(UpdateMakeFileForModuleRevisionTask(env))
    
    def _finished(self):
        # TODO: This isn't a real Task, and so having a _finished() just creates problems.
        pass
    
    def _execute(self):
        pass

class PanopolyPreReleaseTask(Task):
    def _finished(self):
        return get_latest_commit_message(self.env['root']) in self.env['messages']

    def _execute(self):
        # Stash the existing make file temporarily.
        shutil.copy(self.env['make_file'], self.env['temp_make_file'])

        # Copy the release make file into place.
        update_makefile_version(self.env['release_make_file'], self.env['new_version'])
        shutil.copy(self.env['release_make_file'], self.env['make_file'])

        os.chdir(self.env['root'])
        execute_cmd("git commit -a -m '%s'" % self.env['messages'][0])

class PanopolyPostReleaseTask(Task):
    def _finished(self):
        return get_latest_commit_message(self.env['root']) == self.env['messages'][1]

    def _execute(self):
        # Copy the temporary make file back and commit.
        shutil.copy(self.env['temp_make_file'], self.env['make_file'])
        os.unlink(self.env['temp_make_file'])

        os.chdir(self.env['root'])
        execute_cmd("git commit -a -m '%s'" % self.env['messages'][1])

class PanopolyProfileReleaseTask(Task):
    modules = [
        'panopoly_admin',
        'panopoly_core',
        'panopoly_demo',
        'panopoly_images',
        'panopoly_magic',
        'panopoly_pages',
        'panopoly_search',
        'panopoly_theme',
        'panopoly_users',
        'panopoly_widgets',
        'panopoly_wysiwyg',
    ]

    def __init__(self, *args, **kw):
        Task.__init__(self, *args, **kw)

        short_version = self.env['new_version'].split('-')[1]
        my_root = os.path.join(self.env['root'], 'panopoly')
        env = self.env.clone(
            root = my_root,
            make_file = os.path.join(my_root, 'drupal-org.make'),
            temp_make_file = os.path.join(my_root, 'drupal-org-temporary.make'),
            release_make_file = os.path.join(my_root, 'drupal-org-release.make'),
            short_version = short_version,
            messages = [
                "Getting ready for the %s release" % short_version,
                "Restored drupal-org.make after the %s release" % short_version,
            ],
            modules = self.modules
        )

        self.dependencies.append(GitCloneTask(env))

        for project_name in self.modules:
            module_env = env.clone(
                root = os.path.join(self.env['root'], project_name),
                project_name = project_name
            )
            self.dependencies.append(PanopolyModuleReleaseTask(module_env))
        
        self.dependencies.append(PanopolyPreReleaseTask(env))
        self.dependencies.append(GitTagTask(env))
        if self.env['push']:
            self.dependencies.append(GitPushTagTask(env))
        self.dependencies.append(PanopolyPostReleaseTask(env))
        # TODO: Make the actual release on Drupal.org.
        #self.dependencies.append(CreateReleaseTask(env))
        
    def _finished(self):
        # TODO: This isn't a real Task, and so having a _finished() just creates problems.
        pass

    def _execute(self):
        pass

def main():
    import argparse
    import tempfile
    import pprint
    import sys

    parser = argparse.ArgumentParser(description='Make a new release of Panopoly')
    parser.add_argument('old_version', help='The previous version string (ex. 7.x-1.2)')
    parser.add_argument('new_version', help='The previous version string (ex. 7.x-1.3)')
    parser.add_argument('--root', '-r', dest='root', required=True, help='The path to pull all the git repos.')
    parser.add_argument('--username', '-u', dest='username', required=True, help='Your Drupal.org username')
    parser.add_argument('--drush', dest='drush', default='drush', help='Path to your drush executable')
    parser.add_argument('--branch', dest='branch', default='7.x-1.x', help='The branch that the new version is on')
    parser.add_argument('--push', dest='push', default=False, action='store_true', help='The branch that the new version is on')
    args = parser.parse_args()

    # Create a temporary directory to pull all the code and do our magic.
    #root = tempfile.mkdtemp(prefix="panopoly-release-")

    env = Environment({
        'old_version': args.old_version,
        'new_version': args.new_version,
        'username': args.username,
        'root': os.path.abspath(args.root),
        'drush': args.drush,
        'branch': args.branch,
        'push': args.push,
        'project_name': 'panopoly',
    })

    task = PanopolyProfileReleaseTask(env)

    try:
        task.execute()
    except TaskExecutionError, e:
        print >> sys.stderr, e.original
        print >> sys.stderr, e.task.__class__.__name__, ':'
        print >> sys.stderr, pprint.pprint(e.env.items())

if __name__ == '__main__': main()

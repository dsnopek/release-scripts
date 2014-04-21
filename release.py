
import os
import datetime

from Microbuild import Environment, Task, TaskExecutionError, execute_cmd, check_cmd

def update_makefile_revision(make_file, project_name, revision):
    lines = []

    with open(make_file, 'rt') as fd:
        for line in fd.readlines():
            if re.match(r'^projects[%s][download][revision]' % project_name, line):
                line.append('projects[%s][download][revision] = %s\n' % (project_name, revision))
            else:
                line.append(line)

    with open(make_file, 'wt') as fd:
        for line in lines:
            fd.write(line)

class GitCloneTask(Task):
    def _finished(self):
        return os.path.exists(os.path.join(self.env['root'], '.git', 'config'))
    
    def _execute(self):
        repo = "%s@git.drupal.org:project/%s.git" % (self.env['username'], self.env['project_name'])
        execute_cmd("git clone %s %s" % (repo, self.env['root']))

class GitTagTask(Task):
    def _finished(self):
        os.chdir(self.env['root'])
        #tags = execute_cmd("git ls-remote --tags origin", capture=True)
        #return self.env['new_version'] in tags
        return check_cmd("git rev-parse %(new_version)s >/dev/null 2>&1" % self.env)

    def _execute(self):
        os.chdir(self.env['root'])
        execute_cmd("git tag %(new_version)s" % self.env)
        #execute_cmd("git push")

class UpdateReleaseNotesTask(Task):
    def _open(self, mode):
        return open(os.path.join(self.env['root'], 'CHANGELOG.txt'), mode)
    
    def _clean_slate(self):
        os.chdir(self.env['root'])

        # Ensure that we are on the right branch and have no left over changes.
        execute_cmd("git checkout %(branch)s" % self.env)
        execute_cmd("git reset --hard")
        execute_cmd("git checkout -- .")

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

        # TODO: Update the drupal-org.make with the new revision.

class CreateReleaseTask(Task):
    pass

class PanopolyModuleReleaseTask(Task):
    def __init__(self, *args, **kw):
        Task.__init__(self, *args, **kw)

        self.dependencies.append(GitCloneTask(self.env))
        self.dependencies.append(UpdateReleaseNotesTask(self.env))
        self.dependencies.append(GitTagTask(self.env))
    
    def _finished(self):
        pass
    
    def _execute(self):
        pass

class PanopolyProfileReleaseTask(Task):
    modules = [
        'panopoly_admin',
        'panopoly_core',
        'panopoly_demo',
        'panopoly_images',
        'panopoly_magic',
        'panopoly_news',
        'panopoly_pages',
        'panopoly_search',
        'panopoly_theme',
        'panopoly_users',
        'panopoly_widgets',
        'panopoly_wysiwyg',
    ]

    def __init__(self, *args, **kw):
        Task.__init__(self, *args, **kw)

        env = self.env.clone(root=os.path.join(self.env['root'], 'panopoly'))
        self.dependencies.append(GitCloneTask(env))

        for project_name in self.modules:
            module_env = self.env.clone(
                root = os.path.join(self.env['root'], project_name),
                project_name = project_name,
                make_file = os.path.join(env['root'], 'drupal-org.make'),
            )
            self.dependencies.append(PanopolyModuleReleaseTask(module_env))
        
        # TODO: Commit the drupal-org.make for the new -dev versions.
        # TODO: Copy the drupal-org.make away, make a new one with proper versions, commit/tag, copy back commit.
        # TODO: Make the actual release on Drupal.org.
        #self.dependencies.append(UpdateReleaseNotesTask(env))

    def _finished(self):
        # TODO: check online to see if the release exists
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


import os
import shutil
import re
import datetime
import textwrap

import pyotp
import mechanize

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

def browser_selectForm(br, form_id):
    for form in br.forms():
        control = form.find_control("form_id")
        if control.value == form_id:
            br.form = form
            return
    raise ValueError("Unable to find form: %s" % form_id)

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
        execute_cmd("git push", retry=2)
        execute_cmd("git push --tags", retry=2)

class GitCommitTask(Task):
    def _finished(self):
        os.chdir(self.env['root'])
        # TODO: This is a temporary hack to help us get through the 1.8 release - remove after!
        #return False
        commits = execute_cmd("git log --oneline --grep='%(commit_message)s'" % self.env, capture=True)
        return self.env['commit_message'] in commits

    def _execute(self):
        os.chdir(self.env['root'])
        execute_cmd("git commit -a -m '%(commit_message)s'" % self.env)

class UpdateChangelogTask(Task):
    def _open(self, mode):
        return open(os.path.join(self.env['root'], 'CHANGELOG.txt'), mode)
    
    def _finished(self):
        os.chdir(self.env['root'])

        # See if the version is mentioned in the first 30 characters
        with self._open('rt') as fd:
            return self.env['new_version'] in fd.read(100)

    def _wrap_changelog_entry(self, entry):
        lines = []
        for line in entry.split('\n'):
            result = textwrap.wrap(line, width=80, subsequent_indent='  ', replace_whitespace=False)
            lines = lines + result
        return '\n'.join(lines)

    def _execute(self):
        os.chdir(self.env['root'])

        with self._open('rt') as fd:
            changelog = fd.read()

        entry = execute_cmd("%(drush)s rn %(old_version)s %(branch)s --changelog" % self.env, capture=True)
        entry = entry.replace(self.env['branch'], self.env['new_version'])
        entry = entry.replace('%ad', datetime.date.today().strftime('%Y-%m-%d'))
        entry = entry.replace('\n- .\n', '\n- No changes since last release.\n')
        entry = self._wrap_changelog_entry(entry)
        entry = entry + '\n\n'

        with self._open('wt') as fd:
            fd.write(entry)
            fd.write(changelog)

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
    def _finished(self):
        releases = execute_cmd("%(drush)s pm-releases %(project_name)s" % self.env, capture=True)
        return self.env['new_version'] in releases
    
    def _browser(self):
        br = mechanize.Browser()
        # Sorry Drupal.org, we have to ignore robots.txt to get this done.
        br.set_handle_robots(False)

        # First, we have to login.
        br.open("https://drupal.org/user/login")
        browser_selectForm(br, 'user_login')
        br['name'] = self.env['username']
        br['pass'] = self.env['password']
        response = br.submit()

        # Attempt to provide the TFA code.
        if response.geturl().startswith('https://www.drupal.org/system/tfa'):
            browser_selectForm(br, 'tfa_form')
            code = str(pyotp.TOTP(self.env['secret']).now()).zfill(6)
            br['code'] = code
            response = br.submit()

        # If we haven't landed on our user page, then we assume this has failed.
        if response.geturl() != 'https://www.drupal.org/user':
            raise Exception("Login failed.")
        
        return br

    def _execute(self):
        br = self._browser()

        # Navigate to the 'Add new release' form and select the release tag.
        br.open("https://drupal.org/project/" + self.env['project_name'])
        br.follow_link(br.find_link(text='Add new release'))
        browser_selectForm(br, 'project_release_node_form')
        #control = br.form.find_control("versioncontrol_release_label_id")
        # TODO: This is temporary for the 1.8 release - remove afterward...
        try:
          control = br.form.find_control("versioncontrol_release_label_id")
        except:
          return
        found = False
        for item in control.items:
            if item.get_labels()[0].text == self.env['new_version']:
                br['versioncontrol_release_label_id'] = [item.name]
                found = True
                break
        if not found:
            raise Exception("Version not found.")
        response = br.submit()

        # Set the release notes and actually submit.
        browser_selectForm(br, 'project_release_node_form')
        br['body[und][0][value]'] = execute_cmd('%(drush)s rn %(old_version)s %(new_version)s' % self.env, capture=True)
        response = br.submit()
        

class PanopolyModuleReleaseTask(Task):
    def __init__(self, *args, **kw):
        Task.__init__(self, *args, **kw)

        env = self.env

        self.dependencies.append(GitCloneTask(env))
        self.dependencies.append(UpdateChangelogTask(env))
        self.dependencies.append(GitCommitTask(env.clone(commit_message='Updated CHANGELOG.txt for %(new_version)s release.' % env)))
        self.dependencies.append(GitTagTask(env))
        if self.env['push']:
            self.dependencies.append(GitPushTagTask(env))
            self.dependencies.append(CreateReleaseTask(self.env))

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
        update_makefile_version(self.env['release_make_file'], self.env['short_version'])
        shutil.copy(self.env['release_make_file'], self.env['make_file'])

        # Update all the build makefiles as well.
        update_makefile_version(self.env['build_pantheon_make_file'], self.env['short_version'])
        update_makefile_version(self.env['build_panopoly_make_file'], self.env['short_version'])
        update_makefile_version(self.env['build_release_make_file'], self.env['short_version'])

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

        # TODO: update the .travis.yml file to start testing the new release

class PanopolyProfileReleaseTask(Task):
    modules = [
        'panopoly_admin',
        'panopoly_core',
        'panopoly_demo',
        'panopoly_images',
        'panopoly_magic',
        'panopoly_pages',
        'panopoly_search',
        'panopoly_test',
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
            build_pantheon_make_file = os.path.join(my_root, 'build-panopoly-pantheon.make'),
            build_panopoly_make_file = os.path.join(my_root, 'build-panopoly.make'),
            build_release_make_file = os.path.join(my_root, 'build-panopoly-release.make'),
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
        
        self.dependencies.append(UpdateChangelogTask(env))
        self.dependencies.append(PanopolyPreReleaseTask(env))
        self.dependencies.append(GitTagTask(env))
        #if self.env['push']:
        #    self.dependencies.append(GitPushTagTask(env))
        self.dependencies.append(PanopolyPostReleaseTask(env))
        #if self.env['push']:
        #    self.dependencies.append(CreateReleaseTask(env))
        
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
    parser.add_argument('--password', '-p', dest='password', required=True, help='Your Drupal.org password')
    parser.add_argument('--totp-secret', '-s', dest='secret', required=False, help='Your TOTP secret for filling in the TFA code')
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
        'password': args.password,
        'secret': args.secret,
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

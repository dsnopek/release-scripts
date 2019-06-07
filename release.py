
import os
import shutil
import re
import datetime
import textwrap

import pyotp
import mechanize

from Microbuild import Environment, Task, TaskExecutionError, execute_cmd, check_cmd

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
        repo = "git@git.drupal.org:project/%s.git" % (self.env['project_name'],)
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
        return open(self.env.get('changelog_path', os.path.join(self.env['root'], 'CHANGELOG.txt')), mode)
    
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
        # Replace first line.
        entry = "\n".join(entry.split("\n")[1:])
        entry = ("%s, %s\n" % (self.env['new_version'], datetime.date.today().strftime('%Y-%m-%d'))) + entry
        if not '- ' in entry:
            entry = entry.replace('\n\n', '\n- No changes since last release.\n')
        entry = self._wrap_changelog_entry(entry)
        entry = entry + '\n\n'

        with self._open('wt') as fd:
            fd.write(entry)
            fd.write(changelog)

# @todo: remove this global
br = None

class CreateReleaseTask(Task):
    def _finished(self):
        releases = execute_cmd("%(drush)s pm-releases %(project_name)s-7.x" % self.env, capture=True)
        return self.env['new_version'] in releases
    
    def _browser(self):
        # Terrible hack to maintain a single browser - make not use global
        # state later...
        global br
        if br:
            return br

        br = mechanize.Browser()
        # Sorry Drupal.org, we have to ignore robots.txt to get this done.
        br.set_handle_robots(False)

        # First, we have to login.
        br.open("https://drupal.org/user/login")
        browser_selectForm(br, 'user_login')
        br['name'] = self.env['username']
        br['pass'] = self.env['password']
        response = br.submit()
        print 'After username/password:', response.geturl()

        # Attempt to provide the TFA code.
        for i in [1, 2, 3]:
          if response.geturl().startswith('https://www.drupal.org/system/tfa'):
              browser_selectForm(br, 'tfa_form')
              code = str(pyotp.TOTP(self.env['secret']).now()).zfill(6)
              br['code'] = code
              response = br.submit()

          print 'After TFA:', response.geturl()

          if response.geturl() == 'https://www.drupal.org/user':
              break

          if i > 1:
              print response.read()

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
        # TODO: This isn't a safe Task! It's messing with global state (the
        #       CHANGELOG.txt files in the main profile repo).
        if self.env['stage'] == 1:
            self.dependencies.append(UpdateChangelogTask(env))
            if self.env['project_name'] == 'panopoly_demo':
                self.dependencies.append(GitCommitTask(env.clone(commit_message='Updated CHANGELOG.txt for %(new_version)s release.' % env)))
        elif self.env['stage'] == 2:
            self.dependencies.append(GitTagTask(env))
            if self.env['push']:
                self.dependencies.append(GitPushTagTask(env))
                self.dependencies.append(CreateReleaseTask(self.env))

    def _finished(self):
        # TODO: This isn't a real Task, and so having a _finished() just creates problems.
        pass
    
    def _execute(self):
        pass

class PanopolyPreReleaseTask(Task):
    def _finished(self):
        return get_latest_commit_message(self.env['root']) == self.env['message']

    def _execute(self):
        os.chdir(self.env['root'])
        execute_cmd("git commit -a -m '%s'" % self.env['message'])

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
            short_version = short_version,
            message = "Getting ready for the %s release" % short_version,
            modules = self.modules
        )

        self.dependencies.append(GitCloneTask(env))

        for project_name in self.modules:
            project_root = os.path.join(self.env['root'], project_name)
            if project_name == 'panopoly_demo':
                changelog_path = os.path.join(project_root, 'CHANGELOG.txt')
            else:
                changelog_path = os.path.join(self.env['root'], 'panopoly', 'modules', 'panopoly', project_name, 'CHANGELOG.txt')
            module_env = env.clone(
                root = project_root,
                project_name = project_name,
                changelog_path = changelog_path
            )
            self.dependencies.append(PanopolyModuleReleaseTask(module_env))
        
        if self.env['stage'] == 1:
            self.dependencies.append(UpdateChangelogTask(env))
            self.dependencies.append(PanopolyPreReleaseTask(env))
            self.dependencies.append(GitTagTask(env))

            #if self.env['push']:
            #    self.dependencies.append(GitPushTagTask(env))
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
    parser.add_argument('--stage', dest='stage', type=int, default=1, required=True, help='The release stage (1 or 2)')
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
        # Stage 1: Get the profile ready to release
        # Stage 2: Release the panopoly_* modules
        'stage': args.stage,
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

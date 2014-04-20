
from Microbuild import Environment, Task

class UpdateReleaseNotesTask(Task):
    def _finished(self):
        pass
    
    def _execute(self):
        pass

class PanopolyReleaseTask(Task):
    def __init__(self, *args, **kw):
        Task.__init__(*args, **kw)

        env = self.env.clone(root=os.path.join(self.env['root'], 'panopoly'))

        self.dependencies.append(UpdateReleaseNotesTask(env))

    def _finished(self):
        # TODO: check online to see if the release exists
        pass

    def _execute(self):
        pass

def main():
    import argparse

    parser = argparse.ArgumentParser(description='Make a new release of Panopoly')
    parser.add_argument('old_version', help='The previous version string (ex. 7.x-1.2)')
    parser.add_argument('new_version', help='The previous version string (ex. 7.x-1.3)')
    args = parser.parse_args()

    # TODO: Create a temporary directory to pull all the code and do our magic.

    env = Environment({
        'old_version': args.old_version,
        'new_version': args.new_version,
        'root': '',
    })

    task = PanopolyReleaseTask(env)
    task.execute()

if __name__ == '__main__': main()

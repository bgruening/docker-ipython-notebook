#!/usr/bin/env python
from bioblend.galaxy import objects
from bioblend.galaxy import GalaxyInstance
from bioblend.galaxy.histories import HistoryClient
from bioblend.galaxy.datasets import DatasetClient
import subprocess
import argparse
import os
from string import Template
import logging
DEBUG = os.environ.get('DEBUG', "False").lower() == 'true'
if DEBUG:
    logging.basicConfig(level=logging.DEBUG)
logging.getLogger("bioblend").setLevel(logging.CRITICAL)
log = logging.getLogger()


def _get_ip():
    """Get IP address for the docker host
    """
    cmd_netstat = ['netstat','-nr']
    p1 = subprocess.Popen(cmd_netstat, stdout=subprocess.PIPE)
    cmd_grep = ['grep', '^0\.0\.0\.0']
    p2 = subprocess.Popen(cmd_grep, stdin=p1.stdout, stdout=subprocess.PIPE)
    cmd_awk = ['awk', '{ print $2 }']
    p3 = subprocess.Popen(cmd_awk, stdin=p2.stdout, stdout=subprocess.PIPE)
    galaxy_ip = p3.stdout.read()
    log.debug('Host IP determined to be %s', galaxy_ip)
    return galaxy_ip


def _test_url(url, key, history_id, obj=True):
    """Test the functionality of a given galaxy URL, to ensure we can connect
    on that address."""
    try:
        if obj:
            gi = objects.GalaxyInstance(url, key)
            gi.histories.get(history_id)
        else:
            gi = GalaxyInstance(url=url, key=key)
            gi.histories.get_histories(history_id)
        return gi
    except Exception:
        return None


def get_galaxy_connection(history_id=None, obj=True):
    """
        Given access to the configuration dict that galaxy passed us, we try and connect to galaxy's API.

        First we try connecting to galaxy directly, using an IP address given
        us by docker (since the galaxy host is the default gateway for docker).
        Using additional information collected by galaxy like the port it is
        running on and the application path, we build a galaxy URL and test our
        connection by attempting to get a history listing. This is done to
        avoid any nasty network configuration that a SysAdmin has placed
        between galaxy and us inside docker, like disabling API queries.

        If that fails, we failover to using the URL the user is accessing
        through. This will succeed where the previous connection fails under
        the conditions of REMOTE_USER and galaxy running under uWSGI.
    """
    history_id = history_id or os.environ['HISTORY_ID']
    key = os.environ['API_KEY']

    ### Customised/Raw galaxy_url ###
    galaxy_ip = _get_ip()
    # Substitute $DOCKER_HOST with real IP
    url = Template(os.environ['GALAXY_URL']).safe_substitute({'DOCKER_HOST': galaxy_ip})
    gi = _test_url(url, key, history_id, obj=obj)
    if gi is not None:
        return gi

    ### Failover, fully auto-detected URL ###
    # Remove trailing slashes
    app_path = os.environ['GALAXY_URL'].rstrip('/')
    # Remove protocol+host:port if included
    app_path = ''.join(app_path.split('/')[3:])

    if 'GALAXY_WEB_PORT' not in os.environ:
        # We've failed to detect a port in the config we were given by
        # galaxy, so we won't be able to construct a valid URL
        raise Exception("No port")
    else:
        # We should be able to find a port to connect to galaxy on via this
        # conf var: galaxy_paster_port
        galaxy_port = os.environ['GALAXY_WEB_PORT']

    built_galaxy_url = 'http://%s:%s/%s' %  (galaxy_ip.strip(), galaxy_port, app_path.strip())
    url = built_galaxy_url.rstrip('/')

    gi = _test_url(url, key, history_id, obj=obj)
    if gi is not None:
        return gi

    ### Fail ###
    msg = "Could not connect to a galaxy instance. Please contact your SysAdmin for help with this error"
    raise Exception(msg)


def put(filename, file_type='auto', history_id=None):
    """
        Given a filename of any file accessible to the docker instance, this
        function will upload that file to galaxy using the current history.
        Does not return anything.
    """
    gi = get_galaxy_connection(history_id=history_id)
    history_id = history_id or os.environ['HISTORY_ID']
    history = gi.histories.get( history_id )
    history.upload_dataset(filename, file_type=file_type)


def get(dataset_id, history_id=None):
    """
        Given the history_id that is displayed to the user, this function will
        download the file from the history and stores it under /import/
        Return value is the path to the dataset stored under /import/
    """
    history_id = history_id or os.environ['HISTORY_ID']
    # The object version of bioblend is to slow in retrieving all datasets from a history
    # fallback to the non-object path
    gi = get_galaxy_connection(history_id=history_id, obj=False)

    file_path = '/import/%s' % dataset_id

    # Cache the file requests. E.g. in the example of someone doing something
    # silly like a get() for a Galaxy file in a for-loop, wouldn't want to
    # re-download every time and add that overhead.
    if not os.path.exists(file_path):
        hc = HistoryClient(gi)
        dc = DatasetClient(gi)
        history = hc.show_history(history_id, contents=True)
        datasets = {ds['hid']: ds['id'] for ds in history}
        dc.download_dataset( datasets[dataset_id], file_path=file_path, use_default_filename=False )

    return file_path


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Connect to Galaxy through the API')
    parser.add_argument('--action',   help='Action to execute', choices=['get', 'put'])
    parser.add_argument('--argument', help='File/ID number to Upload/Download, respectively')
    parser.add_argument('--history-id', dest="history_id", default=None,
        help='History ID. The history ID and the dataset ID uniquly identify a dataset. Per default this is set to the current Galaxy history.')
    parser.add_argument('-t', '--filetype', help='Galaxy file format. If not specified Galaxy will try to guess the filetype automatically.', default='auto')
    args = parser.parse_args()

    if args.action == 'get':
        # Ensure it's a numerical value
        get(int(args.argument), history_id=args.history_id)
    elif args.action == 'put':
        put(args.argument, file_type=args.filetype, history_id=args.history_id)

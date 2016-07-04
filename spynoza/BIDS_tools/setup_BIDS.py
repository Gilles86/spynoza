from __future__ import print_function

import json
import os
import os.path as op
import shutil
import subprocess
import urllib
import warnings
import fnmatch
import gzip
from collections import OrderedDict
from copy import copy, deepcopy
from glob import glob
from joblib import Parallel, delayed
from raw2nifti import parrec2nii
from behav2tsv import Pres2tsv
from utils import check_executable

class BIDSConstructor(object):

    def __init__(self, project_dir, cfg_file):
        """ Initializes a BIDSConstructor object. """

        self.project_dir = project_dir
        self.cfg_file = cfg_file
        self.dcm2niix = check_executable('dcm2niix')
        self.edf2asc = check_executable('edf2asc')
        self.sub_dirs = None
        self.cfg = None
        self.mappings = None
        self.debug = None

        if not self.dcm2niix:
            msg = "The program 'dcm2niix' was not found on this computer; attempting to " \
                  "convert mri-files to nifti using nibabel."
            warnings.warn(msg)

        if not self.edf2asc:
            msg = "The program 'edf2asc' was not found on this computer; cannot convert " \
                  "edf-files!"
            warnings.warn(msg)

    def convert2bids(self):
        """ Method to call conversion process. """

        self._parse_cfg_file()
        self.sub_dirs = glob(op.join(self.project_dir, 'sub*'))

        if not self.sub_dirs:
            msg = "Could not find subdirs in %s. " \
                  "Make sure they're named 'sub-<nr>.'" % self.project_dir
            raise ValueError(msg)

        if self.cfg['options']['backup']:
            print("Performing back-up.")
            self._backup()

        for sub_dir in self.sub_dirs:

            sub_name = op.basename(sub_dir)
            print("Processing %s" % sub_name)

            sess_dirs = glob(op.join(sub_dir, 'ses*'))

            if not sess_dirs:
                # If there are no session dirs,  use sub_dir
                sess_dirs = [sub_dir]

            for sess_dir in sess_dirs:

                data_types = [c for c in self.cfg.keys() if c in ['func', 'anat', 'dwi', 'fmap']]
                _ = [self._move_and_rename(sess_dir, dtype, sub_name) for dtype in data_types]
                _ = [self._transform(sess_dir, dtype) for dtype in data_types]

                unalloc_files = [f for f in glob(op.join(sess_dir, '*')) if not op.isdir(f)]
                if unalloc_files:
                    print('Unallocated files found in %s:' % sess_dir)
                    _ = [print(f) for f in unalloc_files]

    def _parse_cfg_file(self):
        """ Parses config file and sets defaults. """

        with open(self.cfg_file) as config:
            self.cfg = json.load(config, object_pairs_hook=OrderedDict)

        # Definition of sensible defaults
        if not 'backup' in self.cfg['options']:
            self.cfg['options']['backup'] = 0

        if not 'mri_type' in self.cfg['options']:
            self.cfg['options']['mri_type'] = 'parrec'

        if not 'n_cores' in self.cfg['options']:
            self.cfg['options']['n_cores'] = -1

        self.mappings = self.cfg['mappings']
        self.debug = self.cfg['options']['debug']

    def _backup(self):
        """ Backs up raw data into separate dir. """

        dirs = [d for d in glob(op.join(self.project_dir, 'sub-*')) if op.isdir(d)]
        backup_dir = self._make_dir(op.join(self.project_dir, 'backup_raw'))

        for d in dirs:
            dest_dir = op.join(backup_dir, op.basename(d))
            if op.isdir(d) and not op.isdir(dest_dir):
                shutil.copytree(d, dest_dir)

    def _move_and_rename(self, sess_dir, dtype, sub_name):
        """ Does the actual work of processing/renaming/conversion. """
        n_elem = len(self.cfg[dtype])

        if n_elem > 0:
            data_dir = self._make_dir(op.join(sess_dir, dtype))
        else:
            return 0

        # Loop over contents of func/anat/dwi/fieldmap
        for elem in self.cfg[dtype].keys():

            # Kinda ugly, but can't figure out a more elegant way atm
            kv_pairs = deepcopy(self.cfg[dtype][elem])
            idf = deepcopy(kv_pairs['id'])
            del kv_pairs['id']

            # common_name is simply sub-xxx
            common_name = copy(sub_name)

            for key, value in kv_pairs.iteritems():

                # Append key-value pair if it's not an empty string
                if value and key != 'mapping':
                    common_name += '_%s-%s' % (key, value)
                elif key == 'mapping':
                    common_name += '_%s' % value

            # Find files corresponding to func/anat/dwi/fieldmap
            files = glob(op.join(sess_dir, '*%s*' % idf))

            for f in files:
                # Rename files according to mapping

                types = []
                for ftype, match in self.mappings.iteritems():
                    match = '*' + match + '*'

                    if fnmatch.fnmatch(f, match):
                        types.append(ftype)

                if len(types) > 1:
                    msg = "Couldn't determine file-type for file '%s'; is one of the "\
                          "following:\n %r" % (f, types)
                    warnings.warn(msg)
                elif len(types) == 1:
                    ftype = types[0]
                else:
                    # No file found; ends up in unallocated (printed later).
                    pass

                # Create full name as common_name + unique filetype + original extension
                exts = f.split('.')[1:]

                # For some weird reason, people seem to use periods in filenames,
                # so remove all unnecessary 'extensions'
                allowed_exts = ['par', 'rec', 'nii', 'gz', 'dcm', 'pickle', 'json',
                                'edf', 'log', 'bz2', 'tar']
                upper_exts = [s.upper() for s in allowed_exts]
                allowed_exts.extend(upper_exts)

                clean_exts = '.'.join([e for e in exts if e in allowed_exts])
                full_name = op.join(data_dir, common_name + '_%s.%s' % (ftype, clean_exts))
                full_name = full_name.replace('_b0', '')

                if self.debug:
                    print("Renaming '%s' as '%s'" % (f, full_name))

                os.rename(f, full_name)

    def _transform(self, sess_dir, dtype):

        # Convert stuff to bids-compatible formats (e.g. nii.gz, .tsv, etc.)
        data_dir = op.join(sess_dir, dtype)
        self._mri2nifti(data_dir, n_cores=self.cfg['options']['n_cores'])
        self._log2tsv(data_dir, type=self.cfg['options']['log_type'])
        self._edf2tsv(data_dir)

        # Move topup files to fmap directory
        topups = glob(op.join(data_dir, '*_topup*'))

        if topups and dtype != 'fmap':
            dest = self._make_dir(op.join(sess_dir, 'fmap'))
            [shutil.move(tu, dest) for tu in topups]

    def _mri2nifti(self, directory, compress=True, n_cores=-1):
        """ Converts raw mri to nifti.gz. """

        compress = False if self.debug else True

        if self.cfg['options']['mri_type'] == 'parrec':
            PAR_files = self._glob(directory, ['.PAR', '.par'])

            try:
                Parallel(n_jobs=n_cores)(delayed(parrec2nii)(pfile, compress, 'dcm2niix') for pfile in PAR_files)
            except KeyError:
                Parallel(n_jobs=n_cores)(delayed(parrec2nii)(pfile, compress, 'nibabel') for pfile in PAR_files)

        elif self.cfg['options']['mri_type'] == 'nifti':
            niftis = self._glob(directory, ['.nii', '.nifti'])

            if niftis:
                for f in niftis:
                    with open(f, 'rb') as f_in, gzip.open(f + '.gz', 'wb') as f_out:
                        shutil.copyfileobj(f_in, f_out)

                _ = [os.remove(f) for f in niftis if op.exists(f)]

        elif self.cfg['options']['mri_type'] == 'nifti-gz':
            pass

        elif self.cfg['options']['mri_type'] == 'dicom':
            print('DICOM conversion not yet implemented!')
        else:
            print("'%s' conversion not yet supported!" % self.cfg['options']['mri_type'])

    def _log2tsv(self, directory, type='Presentation'):
        """ Converts behavioral logs to event_files. """

        if type == 'Presentation':
            logs = glob(op.join(directory, '*.log'))
            event_dir = op.join(self.project_dir, 'task_info')

            if not op.isdir(event_dir):
                raise IOError("The event_dir '%s' doesnt exist!" % event_dir)

            for log in logs:
                plc = Pres2tsv(in_file=log, event_dir=event_dir)
                plc.parse()

    def _edf2tsv(self, directory):

        idf = self.cfg['mappings']['eyedata']
        if idf:
            edfs = glob(op.join(directory, '*%s*' % idf))

            if edfs:

                pass

    def _make_dir(self, path):
        """ Creates dir-if-not-exists-already. """
        if not op.isdir(path):
            os.makedirs(path)

        return path

    def _glob(self, path, wildcards):

        files = []

        for w in wildcards:
            files.extend(glob(op.join(path, '*%s*' % w)))

        return files

def fetch_example_data(directory=None, type='7T'):

    if directory is None:
        directory = os.getcwd()

    if type == '7T':
        url = "https://surfdrive.surf.nl/files/index.php/s/Lc6pvD0mK6ZNZKo/download"
        # Have to use _new extension because Surfdrive won't let me remove files (argh)
        out_file = op.join(directory, 'testdata_%s_new.zip' % type)
        size_msg = """ The file you will download is ~1.8 GB; do you want to continue? (Y / N): """
    elif type == '3T':
        url = "https://surfdrive.surf.nl/files/index.php/s/prfv4mh2ft01LSN/download"
        out_file = op.join(directory, 'testdata_%s.zip' % type)
        size_msg = """ The file you will download is ~120 MB; do you want to continue? (Y / N): """
    else:
        msg = "Specify for type either '7T' or '3T'"
        raise ValueError(msg)

    if op.exists(out_file):
        return 'Already downloaded!'

    resp = raw_input(size_msg)

    if resp in ['Y', 'y', 'yes', 'Yes']:
        print('Downloading test data (%s) ...' % type, end='')

        out_dir = op.dirname(out_file)
        if not op.isdir(out_dir):
            os.makedirs(out_dir)

        if not op.exists(out_file):
            urllib.urlretrieve(url, out_file)

        with open(os.devnull, 'w') as devnull:
            subprocess.call(['unzip', out_file, '-d', out_dir], stdout=devnull)
            subprocess.call(['rm', out_file], stdout=devnull)

            print(' done.')
            print('Data is located at: %s' % op.join(out_dir, op.basename(out_file)))

    elif resp in ['N', 'n', 'no', 'No']:
        print('Aborting download.')
    else:
        print('Invalid answer! Choose Y or N.')

    return out_dir




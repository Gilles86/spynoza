from __future__ import print_function, division
import os
import os.path as op
import numpy as np
import gzip
import shutil
import json
import subprocess
import nibabel as nib
from ..utils import check_executable
from glob import glob


def parrec2nii(PAR_file, compress=True, backend='dcm2niix'):
    """ Converts par/rec files to nifti.gz. """

    # To do: refactor as class?
    base_dir = op.dirname(PAR_file)
    base_name = op.join(base_dir, op.splitext(PAR_file)[0])
    ni_name = base_name + '.nii'

    REC_file = '%s.REC' % op.splitext(PAR_file)[0]

    # If dcm2niix is available (and is specified), use it to convert par/rec to ni.
    if check_executable('dcm2niix') and backend == 'dcm2niix':

        cmd = ['dcm2niix', '-b', 'y', '-f', op.basename(base_name), PAR_file]
        with open(os.devnull, 'w') as devnull:
            subprocess.call(cmd, stdout=devnull)

        _rename_b0_files(base_dir=base_dir)

    # Otherwise, try nibabel to read in par/rec and write out nifti.
    else:

        with open(PAR_file, 'r') as f:
            hdr = nib.parrec.parse_PAR_header(f)[0]

        for key, value in hdr.iteritems():

            if isinstance(value, (np.ndarray, np.generic)):
                hdr[key] = value.tolist()

        # Extract metadata from par-header to create .json.
        parts = op.basename(base_name).split('_')
        task_pair = [pair for pair in parts if 'task' in pair]
        task_name = task_pair[0].split('-')[-1] if task_pair else 'no task'
        tr = hdr['repetition_time']
        tr = tr if tr > 500 else tr / 1000

        hdr_json = {'RepetitionTime': tr, 'TaskName': task_name}
        fn = base_name + '.json'

        with open(fn, 'w') as to_write:
            json.dump(hdr_json, to_write, indent=4)

        # Write out nifti-file
        PR_obj = nib.parrec.load(REC_file)
        nib.nifti1.save(PR_obj, base_name)

    # Check for nifti-files (existing or newly created) and zip them.
    to_compress = glob(op.join(base_dir, '*.nii'))
    if compress:
        for f in to_compress:
            with open(f, 'rb') as f_in, gzip.open(f + '.gz', 'wb') as f_out:
                shutil.copyfileobj(f_in, f_out)

        _ = [os.remove(f) for f in to_compress if op.exists(f)]
    _ = [os.remove(f) for f in [REC_file] + [PAR_file]]

def _rename_b0_files(base_dir):
    """ Renames b0-files to phasediff and magnitude imgs.

    IMPORTANT: assumes one phasediff and one magnitude img,
    or optionally >1 magnitudes imgs (but not >1 phasediff img).
    """

    jsons = glob(op.join(base_dir, '_ph*.json'))
    if jsons:
        _ = [os.rename(f, f.replace('_ph', '')) for f in jsons]

    b0_files = sorted(glob(op.join(base_dir, '_ph*.nii')))
    if len(b0_files) > 1:

        for i, b0_file in enumerate(b0_files[:-1]):

            if len(b0_files) > 2:
                new_name = b0_file.replace('_ph', '')[:-9] + '_magnitude%i.nii' % (i + 1)
            else:
                new_name = b0_file.replace('_ph', '')[:-9] + '_magnitude.nii'

            os.rename(b0_file, new_name)

        os.rename(b0_files[-1], b0_files[-1].replace('_ph', '')[:-9] + '_phasediff.nii')

    elif len(b0_files) == 1:
        new_name = b0_files[0].replace('_ph', '')
        os.rename(b0_files[0], new_name)

    else:
        # Do nothing if there seem to be no b0-files.
        pass
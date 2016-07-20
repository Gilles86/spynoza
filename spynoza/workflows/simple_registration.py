# coding: utf-8

import os

import nipype.pipeline.engine as pe
from nipype.interfaces import utility as niu
from nipype.interfaces import fsl
from nipype.interfaces.freesurfer import BBRegister
import nipype.interfaces.freesurfer as fs

# setup i/o for workflow
infosource = pe.Node(IdentityInterface(fields=['subject_id']), name="infosource")
infosource.iterables = [('subject_id', subject_list)]
selectfiles = pe.Node(SelectFiles(template, base_directory=wd), name="selectfiles")
datasink = pe.Node(DataSink(base_directory=data_organizer.project_dir, container=datasink_dir), name="datasink")
substitutions = [('_subject_id_', '')]
datasink.inputs.substitutions = substitutions

# create instances of nodes
# T1 nodes
reorient_T1 = pe.Node(interface=fsl.Reorient2Std(), name='reorient_T1')
bet_T1 = pe.Node(interface=fsl.BET(frac=frac_T1, robust=robust_bet), name='bet_T1')
# reorient 2 standard
reorient_epi = pe.MapNode(interface=fsl.Reorient2Std(), name='reorient_epi', iterfield='in_file')
# bet epis
bet_epi = pe.MapNode(interface=fsl.BET(frac=frac_epi, functional=True), name='bet_epi', iterfield='in_file')
# find middle run
findmiddlerun = pe.Node(util.Function(input_names=['in_files'],output_names=['middle_run', 'other_runs'], function=find_middle_run), name='find_middle_run')
# motion correction this volume
mcflirt_middle = pe.Node(interface=fsl.MCFLIRT(cost=mcflt_cost, interpolation='sinc', stages=3, save_plots=True),name='mcflirt_middle')
# motion correct all other runs towards this epi
mcflirt_rest = pe.MapNode(interface=fsl.MCFLIRT(cost=mcflt_cost, interpolation='sinc', stages=3, save_plots=True),name='mcflirt_rest', iterfield='in_file')
# compute mean volume
mean_bold = pe.Node(interface=fsl.maths.MeanImage(dimension='T'), name='mean_bold')
# plot motion
plot_motion = pe.MapNode(interface=fsl.PlotMotionParams(in_source='fsl'),name='plot_motion',iterfield=['in_file'])
# Iterate over rotations and translations!
plot_motion.iterables = ('plot_type', ['rotations', 'translations'])
# flirt this to MNI
flirt_to_mni = pe.Node(fsl.FLIRT(bins=640, cost_func='mutualinfo',in_file='structural.nii',reference='mni.nii',output_type='NIFTI_GZ'))
# fnirt this to MNI
fnirt_to_mni = pe.Node(fsl.FNIRT(affine_file=example_data('trans.mat')))
# and BBregister mean volume to FS dir
bbreg = BBRegister(subject_id='me', source_file='structural.nii', init='header', contrast_type='t2')

# inputnode 
inputnode = pe.Node(niu.IdentityInterface(fields=['subject_id',
                                                      'subjects_dir',
                                                      'T1_files']),
                        name='inputspec')
# recon-all node for T1
autorecon1 = pe.Node(fs.ReconAll(), directive='autorecon1',name="autorecon1")

# now put together in workflow
register = pe.Workflow(name='register')
register.base_dir = data_organizer.project_dir
register.connect(infosource, 'subject_id', selectfiles, 'subject_id')

# T1 processing
register.connect(selectfiles, 'struc', autorecon1, '')
register.connect(selectfiles, 'struc', reorient_T1, 'in_file')
register.connect(reorient_T1, 'out_file', bet_T1, 'in_file')
register.connect(bet_T1, 'out_file', datasink, 'bett1')


register.connect(selectfiles, 'func', reorient_epi, 'in_file')
register.connect(reorient_epi, 'out_file', bet_epi, 'in_file')

register.connect(bet_epi, 'out_file', findmiddlerun, 'in_files')
register.connect(findmiddlerun, 'middle_run', mcflirt_middle, 'in_file')
register.connect(mcflirt_middle, 'out_file', mean_bold, 'in_file')
register.connect(mean_bold, 'out_file', mcflirt_rest, 'ref_file')
register.connect(findmiddlerun, 'other_runs', mcflirt_rest, 'in_file')
register.connect(mcflirt_middle, 'out_file', mcflirt_merger_infiles, 'in1')
register.connect(mcflirt_rest, 'out_file', mcflirt_merger_infiles, 'in2')
register.connect(mcflirt_merger_infiles, 'out', slicetimer, 'in_file')

register.connect(mcflirt_middle, 'par_file', mcflirt_merger_motionplots, 'in1')
register.connect(mcflirt_rest, 'par_file', mcflirt_merger_motionplots, 'in2')
register.connect(mcflirt_merger_motionplots, 'out', plot_motion, 'in_file')
register.connect(plot_motion, 'out_file', datasink, 'mcplots')
register.connect(mean_bold, 'out_file', datasink, 'meanbold')

register.connect(slicetimer, 'slice_time_corrected_file', smooth, 'in_file')
register.connect(smooth, 'out_file', sgfilter, 'in_file')
register.connect(selectfiles, 'func', extract_scaninfo, 'in_file')
register.connect(extract_scaninfo, 'TR', slicetimer, 'time_repetition')

register.connect(sgfilter, 'out_file', datasink, 'fullyregister')

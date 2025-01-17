from __future__ import absolute_import, division, print_function
from six.moves import range
# -*- Mode: Python; c-basic-offset: 2; indent-tabs-mode: nil; tab-width: 8 -*-
#
# LIBTBX_SET_DISPATCHER_NAME cxi.mpi_submit
# LIBTBX_SET_DISPATCHER_NAME cctbx.xfel.mpi_submit
#
# Submit a cctbx.xfel processing job to the cluster using bsub, after first creating
# an appropiate trial directory in the requested location and copying and modifying
# the given psana config files and cctbx.xfel phil files
#
import os, sys, shutil
from libtbx.utils import Sorry
from libtbx.phil import parse
from libtbx import easy_run
from xfel.util.mp import mp_phil_str, get_submit_command_chooser

help_str = """
Use cxi.mpi_submit to submit a cctbx.xfel job to a cluster for analyzing
diffraction data.  cxi.mpi_submit will create a directory for
you in the location specified by output.output_dir using the run and trial
numbers specified.  If no trial number is specified, one will be chosen auto-
matically by examining the output directory.  The output directory will be
created if it doesn't exist.

Examples (LCLS, for non-LCLS see below):

cxi.mpi_submit input.cfg=cxi49812/thermo.cfg input.experiment=cxi49812 \\
  input.run_num=25 output.output_dir=\\
  /reg/d/psdm/cxi/cxi49812/results/username/results mp.nproc=100 mp.queue=psanaq

This will submit run 25 of experiment cxi49812 to the psana queue for
processing using the modules specified by cxi49812/thermo.cfg.

cxi.mpi_submit submit.phil

Use the phil parameters in submit.phil to control job submission.

Before the job is submitted, the following occurs:

Directory /reg/d/psdm/cxi/cxi49812/results/username/results is created if it
doesn't exist.

Directory /reg/d/psdm/cxi/cxi49812/results/username/results/r0025 is created if it
doesn't exist.

The run directory is searched for the next available trial ID, say 003.  That
directory is created.

Under /reg/d/psdm/cxi/cxi49812/results/username/results/r0025/003, the following is
created:

Directory stdout.  The log for the experiment will go here.
File psana_orig.cfg.  This is a verbatim copy of the input config file.
File psana.cfg.  This is a copy of the input config file, re-written to respect
the new paths for this trial.  Any parameters with _dirname are re-written
to include this trial directory and the associated directories are created. Any
phil files found are copied to the trial directory, renamed, and updated.  This
includes phil files included by copied phil files.

The final bsub command is saved in submit.sh for future use.

For processing non-LCLS data, a different dispatcher and any non-default queueing
and multiprocessing parameters should be explicitly requested. For example,
mp.env_script should be specified on any system where the interactive environment
is not propagated to the queued jobs.

Example for processing XFEL stills at SACLA:

cxi.mpi_submit /path/to/h5/runs/directory/266708-0/run*.h5 \\
  input.dispatcher=dials.stills_process output.output_dir=/path/to/results \\
  input.target=/path/to//stills_process.phil input.trial=0 \\
  mp.method=pbs mp.queue=psmall mp.nnodes=10 mp.nproc_per_node=28 \\
  mp.env_script=/path/to/setpaths.sh
"""


phil_str = '''
  dry_run = False
    .type = bool
    .help = If True, the program will create the trial directory but not submit the job, \
            and will show the command that would have been executed.
  input {
    experiment = None
      .type = str
      .help = Experiment identifier, e.g. cxi84914 (mandatory for xtc stream processing)
    run_num = None
      .type = str
      .help = Run number to process. Can be a string instead of a number.
    trial = None
      .type = int
      .help = Trial number for this job.  Leave blank to auto determine.
    rungroup = None
      .type = int
      .help = Optional. If used, will add _rgXXX to the trial path. Useful for organizing \
              runs with similar parameters into logical groupings.
    dispatcher = cctbx.xfel.xtc_process
      .type = str
      .help = Which program to run. cxi.xtc_process is for module only based processing, \
              such as mod_hitfind and LABELIT. cctbx.xfel.xtc_process uses the DIALS back \
              end but converts the raw data to CBF before processing it. Use \
              cctbx.xfel.process to use the XTC streams natively without CBF.
    target = None
      .type = path
      .help = Optional path to phil file with additional parameters to be run by the \
              processing program.
    locator = None
      .type = str
      .help = Locator file needed for cctbx.xfel.process and dials.stills_process to find \
              the XTC streams
  }
  output {
    output_dir = "."
      .type = str
      .help = Directory for output files
    split_logs = True
      .type = bool
      .help = Option to split error and log files into separate per process
  }
'''

phil_scope = parse(phil_str + mp_phil_str, process_includes=True)
mp_phil_scope = parse(mp_phil_str, process_includes=True)

def copy_config(config, dest_dir, root_name, params, target_num):
  """ Copy a config file to a directory, and all of its referenced phil files. Recursively
  copies the files and changes the includes statements to match the new file names.
  @param config config file to copy
  @param dest_dir Full path to directory to copy file to
  @param root_name Name to give to copied file. Included phil files will be given
  this name + _N where N is incremented for each file included in the phil file
  @param params phil parameters
  @param target_num for target phil files found in config, latest number found
  """
   # make a copy of the original cfg file
  shutil.copy(config, os.path.join(dest_dir, "%s_orig.cfg"%root_name))

  config_path = os.path.join(dest_dir, "%s.cfg"%root_name)

  # Re-write the config file, changing paths to be relative to the trial directory.
  # Also copy and re-write included phil files, while updating the config file to
  # the new paths
  # Note, some legacy rewrites done by cxi.lsf are not included here.
  f = open(config_path, 'w')
  for line in open(config).readlines():
    if "[pyana]" in line:
      raise Sorry("Pyana not supported. Check your config file.")
    if "RUN_NO" in line:
      line = line.replace("RUN_NO", str(params.input.run_num))
    if "RUN_STR" in line:
      line = line.replace("RUN_STR", "r%04d"%(params.input.run_num))
    if "trial_id" in line:
      key, val = line.split("=")
      line = "%s= %d\n"%(key,params.input.trial)
    if "rungroup_id" in line:
      key, val = line.split("=")
      line = "%s= %s\n"%(key,params.input.rungroup) # None ok
    elif "_dirname" in line:
      key, val = line.split("=")
      val = os.path.join(dest_dir, os.path.basename(val.strip()))
      if not os.path.exists(val):
        os.mkdir(val)
      line = "%s= %s\n"%(key,val)
    elif "xtal_target" in line:
      key, val = line.split("=")
      val = val.strip()
      if not os.path.exists(val):
        raise Sorry("One of the xtal_target files in the cfg file doesn't exist: %s"%val)
      new_target = "params_%d"%target_num
      copy_target(val, dest_dir, new_target)
      target_num += 1
      line = "%s= %s.phil\n"%(key,os.path.join(dest_dir, new_target))
    f.write(line)
  f.close()
  return target_num

def copy_target(target, dest_dir, root_name):
  """ Copy a phil file to a directory, and all of its included phil files. Recursively
  copies the files and changes the includes statements to match the new file names.
  @param target Phil file to copy
  @param dest_dir Full path to directory to copy file to
  @param root_name Name to give to copied file. Included phil files will be given
  this name + _N where N is incremented for each file included in the phil file
  """
  # Each included phil file will be named root_name_N.phil where N is this number,
  # incremented for each included phil file
  num_sub_targets = 1
  f = open(os.path.join(dest_dir, root_name + ".phil"), 'w')
  for line in open(target).readlines():
    if "include" in line:
      inc_str, include_type, sub_target = line.strip().split() # Example: include file cxi-8.2.phil
      if include_type != 'file':
        raise Sorry("Include isn't a file") # FIXME look up what other values are possible here
      sub_target_root_name = "%s_%d"%(root_name, num_sub_targets)
      line = " ".join([inc_str, include_type, sub_target_root_name  + ".phil\n"])
      # recursive call to check for other included files
      copy_target(os.path.join(os.path.dirname(target), sub_target), dest_dir, sub_target_root_name)
      num_sub_targets += 1
    f.write(line)

class Script(object):
  """ Script to submit XFEL data for processing"""
  def __init__(self):
    pass

  def run(self, argv = None):
    """ Set up run folder and submit the job. """
    if argv is None:
      argv = sys.argv[1:]

    if len(argv) == 0 or "-h" in argv or "--help" in argv or "-c" in argv:
      print(help_str)
      print("Showing phil parameters:")
      print(phil_scope.as_str(attributes_level = 2))
      return

    user_phil = []
    dispatcher_args = []
    for arg in argv:
      if (os.path.isfile(arg)):
        try:
          user_phil.append(parse(file_name=arg))
        except Exception as e:
          if os.path.splitext(arg)[1] == ".phil": raise e
          dispatcher_args.append(arg)
      else:
        try:
          user_phil.append(parse(arg))
        except RuntimeError as e:
          dispatcher_args.append(arg)
    scope, unused = phil_scope.fetch(sources=user_phil, track_unused_definitions=True)
    params = scope.extract()
    dispatcher_args.extend(["%s=%s"%(u.path,u.object.words[0].value) for u in unused])

    assert params.input.run_num is not None
    if params.input.dispatcher in ["cxi.xtc_process", "cctbx.xfel.xtc_process"]:
      # processing XTC streams at LCLS -- dispatcher will locate raw data
      assert params.input.experiment is not None or params.input.locator is not None
      print("Submitting run %d of experiment %s"%(int(params.input.run_num), params.input.experiment))
      rundir = os.path.join(params.output.output_dir, "r%04d"%int(params.input.run_num))
    else:
      print("Submitting run %s"%(params.input.run_num))
      try:
        rundir = os.path.join(params.output.output_dir, "r%04d"%int(params.input.run_num))
      except ValueError:
        rundir = os.path.join(params.output.output_dir, params.input.run_num)

    if not os.path.exists(params.output.output_dir):
       os.makedirs(params.output.output_dir)

    if not os.path.exists(rundir):
      os.mkdir(rundir)

    # If a trial number wasn't included, find the next available, up to 999 trials
    if params.input.trial is None:
      found_one = False
      for i in range(1000):
        trialdir = os.path.join(rundir, "%03d"%i)
        if params.input.rungroup is not None:
          trialdir += "_rg%03d"%params.input.rungroup
        if not os.path.exists(trialdir):
          found_one = True
          break
      if found_one:
        params.input.trial = i
      else:
        raise Sorry("All trial numbers in use")
    else:
      trialdir = os.path.join(rundir, "%03d"%params.input.trial)
      if params.input.rungroup is not None:
        trialdir += "_rg%03d"%params.input.rungroup
      if os.path.exists(trialdir):
        raise Sorry("Trial %d already in use"%params.input.trial)

    print("Using trial", params.input.trial)
    os.mkdir(trialdir)

    # log file will live here
    stdoutdir = os.path.join(trialdir, "stdout")
    os.mkdir(stdoutdir)
    logging_str = ""
    if params.output.split_logs:# test parameter for split_log then open and close log file and loop over nprocs
      for i in range(params.mp.nproc):
        error_files = os.path.join(stdoutdir,"error_rank%04d.out"%i)
        log_files = os.path.join(stdoutdir,"log_rank%04d.out"%i)
        open(log_files,'a').close()
        open(error_files,'a').close()
      logging_str = "output.logging_dir=%s"%stdoutdir
    else:
      logging_str = ""

    # Copy any config or phil files specified
    target_num = 1
    has_config = False
    redone_args = []
    for arg in dispatcher_args:
      if not len(arg.split('=')) == 2:
        redone_args.append(arg)
        continue
      name, value = arg.split('=')

      if "cfg" in name and os.path.splitext(value)[1].lower() == ".cfg":
        cfg = value
        if not os.path.exists(cfg):
          raise Sorry("Config file doesn't exist: %s"%cfg)
        if has_config:
          raise Sorry("Multiple config files found")
        has_config = True
        target_num = copy_config(cfg, trialdir, "psana", params, target_num)
        redone_args.append("%s=%s"%(name, os.path.join(trialdir, "psana.cfg")))
      elif "target" in name or os.path.splitext(value)[1].lower() == ".phil":
        phil = value
        if not os.path.exists(phil):
          raise Sorry("Phil file doesn't exist: %s"%phil)
        copy_target(phil, trialdir, "params_%d"%target_num)
        redone_args.append("%s=%s"%(name, os.path.join(trialdir, "params_%d.phil"%target_num)))
        target_num += 1
      else:
        redone_args.append(arg)
    dispatcher_args = redone_args

    # If additional phil params are provided, copy them over too
    if params.input.target is not None:
      if not os.path.exists(params.input.target):
        raise Sorry("Target file doesn't exist: %s"%params.input.target)
      copy_target(params.input.target, trialdir, "params_%d"%target_num)
      params.input.target = os.path.join(trialdir, "params_%d.phil"%target_num)
      target_num += 1

    # Some configs files will specify out_dirname. If not, we want to explicitly create one
    # so the dispatcher will have an output directory.
    output_dir = os.path.join(trialdir, "out")
    if not os.path.exists(output_dir):
      os.makedirs(output_dir)

    # Write out a script for submitting this job and submit it
    submit_path = os.path.join(trialdir, "submit.sh")

    extra_str = ""
    data_str = ""

    assert [params.input.locator, params.input.experiment].count(None) != 0
    if params.input.locator is not None:
      locator_file = os.path.join(trialdir, "data.loc")
      shutil.copyfile(params.input.locator, locator_file)
      data_str += locator_file
    if params.input.experiment is None:
      from xfel.ui import known_dials_dispatchers
      if params.input.dispatcher in known_dials_dispatchers:
        import importlib
        dispatcher_params = importlib.import_module(known_dials_dispatchers[params.input.dispatcher]).phil_scope.extract()
        if hasattr(dispatcher_params, 'input') and hasattr(dispatcher_params.input, 'trial'):
          assert hasattr(dispatcher_params.input, 'run_num')
          data_str += " input.trial=%s input.run_num=%s" % ( # pass along for logging
            params.input.trial, params.input.run_num)
    else:
      data_str += " input.trial=%s input.experiment=%s input.run_num=%s" % (
        params.input.trial, params.input.experiment, params.input.run_num)

    for arg in dispatcher_args:
      extra_str += " %s" % arg

    if params.input.target is not None:
      extra_str += " %s" % params.input.target

    if params.input.rungroup is not None:
      data_str += " input.rungroup=%d" % params.input.rungroup

    command = "%s %s output.output_dir=%s %s %s" % (
      params.input.dispatcher, data_str, output_dir,
      logging_str, extra_str
    )

    job_name = "r%s"%params.input.run_num

    submit_command = get_submit_command_chooser(command, submit_path, stdoutdir, params.mp, job_name=job_name)
    if params.mp.method in ['lsf', 'sge', 'pbs']:
      parts = submit_command.split(" ")
      script = open(parts.pop(-1), "rb")
      run_command = script.read().split("\n")[-2]
      command = " ".join(parts + [run_command])
    else:
      command = submit_command
    print(command)

    if params.dry_run:
      print("Dry run: job not submitted. Trial directory created here:", trialdir)
      print("Execute this command to submit the job:")
      print(submit_command)
    elif params.mp.method == 'local':
      submission_id = os.fork()
      if submission_id > 0:
        return submission_id
      else:
        stdout = os.open(os.path.join(stdoutdir, 'log.out'), os.O_WRONLY|os.O_CREAT|os.O_TRUNC); os.dup2(stdout, 1)
        stderr = os.open(os.path.join(stdoutdir, 'log.err'), os.O_WRONLY|os.O_CREAT|os.O_TRUNC); os.dup2(stderr, 2)
        os.execv(command.split()[0], command.split())
    else:
      try:
        result = easy_run.fully_buffered(command=submit_command)
        result.raise_if_errors()
      except Exception as e:
        if not "Warning: job being submitted without an AFS token." in str(e):
          raise e

      print("Job submitted.  Output in", trialdir)

      if params.mp.method == "mpi" or params.mp.method == "lsf":
        submission_id = None
        for line in result.stdout_lines:
          # example for lsf: 'Job <XXXXXX> is submitted to queue <YYYYYYY>.'
          if len(line.split()) < 2: continue
          s = line.split()[1].lstrip('<').rstrip('>')
          try:
            s = int(s)
          except ValueError:
            pass
          else:
            submission_id = str(s)
        print(submission_id)
        return submission_id
      elif params.mp.method == 'pbs':
        submission_id = "".join(result.stdout_lines).strip()
        print(submission_id)
        return submission_id
    return None

if __name__ == "__main__":
  script = Script()
  script.run()

#!/home/remco/minecraft/captain-hooks/py/bin/python3
import os 
from plumbum.cmd import restic
from rcon.source import Client

RCON_PASSWORD  = os.getenv('RCON_PASSWORD','use the environment var, or enter your password here.')

with Client('127.0.0.1',25575,passwd=RCON_PASSWORD) as mc:
  print('sending /save-all')
  print(mc.run('save-off'))
  print(mc.run('save-all'))
  print('backing up')
  print(restic[os.getenv('HOST'),'-r',os.getenv('URI'),'backup','--tag','files','data']())
  print('allowing saves')
  print(mc.run('save-on'))

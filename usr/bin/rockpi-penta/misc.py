#!/usr/bin/env python3
import re
import os
import sys
import time
import mraa  # pylint: disable=import-error
import shutil
import subprocess
import multiprocessing as mp
from configparser import ConfigParser
from collections import defaultdict, OrderedDict

cmds = {
    'blk': "lsblk | awk '{print $1}'",
    'up': "echo Uptime: `uptime | sed 's/.*up \\([^,]*\\), .*/\\1/'`",
    'temp': "cat /sys/class/thermal/thermal_zone0/temp",
    'ip': "hostname -I | awk '{printf \"IP %s\", $1}'",
    'cpu': "uptime | awk '{printf \"CPU Load: %.2f\", $(NF-2)}'",
    'men': "free -m | awk 'NR==2{printf \"Mem: %s/%sMB\", $3,$2}'",
    'disk': "df -h | awk '$NF==\"/\"{printf \"Disk: %d/%dGB %s\", $3,$2,$5}'"
}

lv2dc = OrderedDict({'lv3': 0, 'lv2': 0.25, 'lv1': 0.5, 'lv0': 0.75})

def set_mode(pin, mode=1):
    try:
        pin = mraa.Gpio(pin)
        pin.dir(mraa.DIR_OUT)
        pin.write(mode)
    except Exception as ex:
        print(ex)
def demote(user_uid, user_gid):
        """Pass the function 'set_ids' to preexec_fn, rather than just calling
        setuid and setgid. This will change the ids for that subprocess only"""

        def set_ids():
                os.setgid(user_gid)
                os.setuid(user_uid)

        return set_ids
def get_username(uid):
    return return subprocess.run("lslogins -u | awk '$1 == {} {{printf $2}}'".format(uid),shell=True, check=True, capture_output=True,text=True).stdout
def check_output(cmd):
    return subprocess.check_output(cmd, shell=True).decode().strip()

def run_output(cmd):
    return return subprocess.run(cmd, shell=True, check=True,capture_output=True,text=True,preexec_fn=demote(conf['user']['gid'],conf['user']['uid'])).stdout
def check_call(cmd):
    return subprocess.check_call(cmd, shell=True)


def get_blk():
    conf['disk'] = [x for x in check_output(cmds['blk']).strip().split('\n') if x.startswith('sd')]


def get_info(s):
    return check_output(cmds[s])


def get_cpu_temp():
    t = float(get_info('temp')) / 1000
    if conf['oled']['f-temp']:
        temp = "CPU Temp: {:.0f}°F".format(t * 1.8 + 32)
    else:
        temp = "CPU Temp: {:.1f}°C".format(t)
    return temp


def read_conf():
    conf = defaultdict(dict)

    try:
        cfg = ConfigParser()
        cfg.read('/etc/rockpi-penta.conf')
        # fan
        conf['fan']['lv0'] = cfg.getfloat('fan', 'lv0')
        conf['fan']['lv1'] = cfg.getfloat('fan', 'lv1')
        conf['fan']['lv2'] = cfg.getfloat('fan', 'lv2')
        conf['fan']['lv3'] = cfg.getfloat('fan', 'lv3')
        # key
        conf['key']['click'] = cfg.get('key', 'click')
        conf['key']['twice'] = cfg.get('key', 'twice')
        conf['key']['press'] = cfg.get('key', 'press')
        # time
        conf['time']['twice'] = cfg.getfloat('time', 'twice')
        conf['time']['press'] = cfg.getfloat('time', 'press')
        # other
        conf['slider']['auto'] = cfg.getboolean('slider', 'auto')
        conf['slider']['time'] = cfg.getfloat('slider', 'time')
        conf['oled']['rotate'] = cfg.getboolean('oled', 'rotate')
        conf['oled']['f-temp'] = cfg.getboolean('oled', 'f-temp')
        #chia-blockchain
        conf['user']['uid'] = cfg.getint('user','user_uid')
        conf['user']['gid'] = cfg.getint('user','user_gid')
        #disks
        conf['disk'] = cfg.get('disk','mnt_points')
    except Exception:
        # fan
        conf['fan']['lv0'] = 35
        conf['fan']['lv1'] = 40
        conf['fan']['lv2'] = 45
        conf['fan']['lv3'] = 50
        # key
        conf['key']['click'] = 'slider'
        conf['key']['twice'] = 'switch'
        conf['key']['press'] = 'none'
        # time
        conf['time']['twice'] = 0.7  # second
        conf['time']['press'] = 1.8
        # other
        conf['slider']['auto'] = True
        conf['slider']['time'] = 10  # second
        conf['oled']['rotate'] = False
        conf['oled']['f-temp'] = False
        #chia-blockchain
        conf['user']['uid'] = 1000
        conf['user']['gid'] = 1000
    return conf


def read_key(pattern, size):
    s = ''
    pin11 = mraa.Gpio(11)
    pin11.dir(mraa.DIR_IN)

    while True:
        s = s[-size:] + str(pin11.read())
        for t, p in pattern.items():
            if p.match(s):
                return t
        time.sleep(0.1)


def watch_key(q=None):
    size = int(conf['time']['press'] * 10)
    wait = int(conf['time']['twice'] * 10)
    pattern = {
        'click': re.compile(r'1+0+1{%d,}' % wait),
        'twice': re.compile(r'1+0+1+0+1{3,}'),
        'press': re.compile(r'1+0{%d,}' % size),
    }

    while True:
        q.put(read_key(pattern, size))

def get_xch_info(cache={}):
    if not cache.get('time') or time.time() - cache['time'] > 3600:
        # need to navigate to folder then activtae venv then run cmd

        cmd = "cd /home/"+get_username(conf['user']['uid'])+"/chia-blockchain/ && . ./activate && chia farm summary | awk '{ if (NR==2||NR==5) {print $4;} else if (NR==1) {print $3;}}' && deactivate"
        xch_list = run_output(cmd).split('\n')
        cache['info_status'] = xch_list[0]
        cache['info_xch'] = xch_list[1]
        cache['info_height'] = xch_list[2]
        cache['time'] = time.time()
    return cache

def get_disk_info(cache={}):
    if not cache.get('time') or time.time() - cache['time'] > 30:
        info = {}
        cmd = "df -h | awk '$NF==\"/\"{printf \"%s\", $5}'"
        info['root'] = check_output(cmd)
        for x in conf['disk']:
            cmd = "df -Bg | awk '$1==\"/dev/{}\" {{printf \"%s\", $5}}'".format(x)
            info[x] = check_output(cmd)
        cache['info'] = list(zip(*info.items()))
        cache['time'] = time.time()

    return cache['info']

def get_disk_info_mnt(cache={}):
    if not cache.get('time') or time.time() - cache['time'] > 30:
        info = {}
        cmd = "df -h | awk '$NF==\"/\"{printf \"%s\", $5}'"
        info['root'] = check_output(cmd)
        for x in conf['disk'].split('|'):
            cmd = "df -Bg | awk '$6==\"{0}\" {{print {1}, $5}}'".format(x,x.split("/")[len(x.split("/"))-1])
            info[x.split("/")[len(x.split("/"))-1]] = check_output(cmd)
        cache['info'] = list(zip(*info.items()))
        cache['time'] = time.time()

    return cache['info']

def slider_next(pages):
    conf['idx'].value += 1
    return pages[conf['idx'].value % len(pages)]


def slider_sleep():
    time.sleep(conf['slider']['time'])


def fan_temp2dc(t):
    for lv, dc in lv2dc.items():
        if t >= conf['fan'][lv]:
            return dc
    return 0.999


def fan_switch():
    conf['run'].value = not(conf['run'].value)

def get_func(key):
    return conf['key'].get(key, 'none')


def open_pwm_i2c():
    def replace(filename, raw_str, new_str):
        with open(filename, 'r') as f:
            content = f.read()

        if raw_str in content:
            shutil.move(filename, filename + '.bak')
            content = content.replace(raw_str, new_str)

            with open(filename, 'w') as f:
                f.write(content)

    replace('/boot/hw_intfc.conf', 'intfc:pwm0=off', 'intfc:pwm0=on')
    replace('/boot/hw_intfc.conf', 'intfc:pwm1=off', 'intfc:pwm1=on')
    replace('/boot/hw_intfc.conf', 'intfc:i2c7=off', 'intfc:i2c7=on')


conf = {'disk': [], 'idx': mp.Value('d', -1), 'run': mp.Value('d', 1)}
conf.update(read_conf())


if __name__ == '__main__':
    if sys.argv[-1] == 'open_pwm_i2c':
        open_pwm_i2c()

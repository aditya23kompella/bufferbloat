from mininet.topo import Topo
from mininet.node import CPULimitedHost, OVSBridge
from mininet.link import TCLink
from mininet.net import Mininet
from mininet.log import lg, info
from mininet.util import dumpNodeConnections
from mininet.cli import CLI

import subprocess
from subprocess import Popen, PIPE
from time import sleep, time
from multiprocessing import Process
from argparse import ArgumentParser

from monitor import monitor_qlen

import sys
import os
import math
from math import sqrt

# Port number of the webserver we are connecting to. Do not change this without also changing the port in webserver.py.
PORT = 80

parser = ArgumentParser(description="Bufferbloat tests")
parser.add_argument('--bw-host', '-B',
                    type=float,
                    help="Bandwidth of host links (Mb/s)",
                    default=1000)

parser.add_argument('--bw-net', '-b',
                    type=float,
                    help="Bandwidth of bottleneck (network) link (Mb/s)",
                    required=True)

parser.add_argument('--delay',
                    type=float,
                    help="Link propagation delay (ms)",
                    required=True)

parser.add_argument('--dir', '-d',
                    help="Directory to store outputs",
                    required=True)

parser.add_argument('--time', '-t',
                    help="Duration (sec) to run the experiment",
                    type=int,
                    default=10)

parser.add_argument('--maxq',
                    type=int,
                    help="Max buffer size of network interface in packets",
                    default=100)

parser.add_argument('--cong',
                    help="Congestion control algorithm to use",
                    default="reno")

# Expt parameters
args = parser.parse_args()

class BBTopo(Topo):
    "Simple topology for bufferbloat experiment."

    def build(self, n=2):
        h1 = self.addHost('h1')
        h2 = self.addHost('h2')

        # Here I have created a switch.  If you change its name, its
        # interface names will change from s0-eth1 to newname-eth1.
        switch = self.addSwitch('s0', cls=OVSBridge)

        # Use args.bw_host for host-to-switch links
        self.addLink(h1, switch, cls=TCLink, bw=args.bw_host)

        self.addLink(switch, h2,
                    cls=TCLink,
                    bw=args.bw_net,
                    delay=f'{args.delay}ms',
                    max_queue_size=args.maxq)

# Simple wrappers around monitoring utilities.

def start_iperf(net):
    h1 = net.get('h1')
    h2 = net.get('h2')
    print("Starting iperf server...")
    # The -w 16m parameter ensures that the TCP flow is not receiver window limited.
    # If it is, there is a chance that the router buffer may not get filled up.
    server = h2.popen("iperf -s -w 16m")

    # Start the iperf client on h1, create a long lived flow.
    client = h1.popen(f"iperf -c {h2.IP()} -t 1000 -i 1")

    return server, client

def start_qmon(iface, interval_sec=0.1, outfile="q.txt"):
    monitor = Process(target=monitor_qlen,
                      args=(iface, interval_sec, outfile))
    monitor.start()
    return monitor

def start_ping(net):
    # Start a ping train from h1 to h2. Measure RTTs every 0.1 second.

    h1 = net.get('h1')
    h2 = net.get('h2')

    # -i 0.1 → 10 pings/sec
    # -c 1000 → total of 1000 pings (~100 sec)
    # Redirect output to file
    ping_cmd = f"ping {h2.IP()} -i 0.1 -c 1000 > {args.dir}/ping.txt"

    # Start the ping train
    h1.popen(ping_cmd, shell=True)

def start_webserver(net):
    h1 = net.get('h1')
    proc = h1.popen("python2 webserver.py", shell=True)  # imports do not work if python3 is used
    sleep(1)
    return [proc]

def verify_url(net):
    h2 = net.get('h2')
    h1 = net.get('h1')

    # Use curl to get status code 
    result = h2.cmd(f"curl -s -o /dev/null -w '%{{http_code}}' http://{h1.IP()}:{PORT}")
    
    if result.strip() == "200":
        return True
    else:
        print(f"Failed to connect: HTTP {result.strip()}")
        return False

def measure_time(net):
    h2 = net.get('h2')
    h1 = net.get('h1')

    if (verify_url(net)):
        cmd = f"curl -o /dev/null -s -w %{{time_total}} http://{h1.IP()}:{PORT}"
        proc = h2.popen(cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        stdout, stderr = proc.communicate()  # wait for it to finish
        return float(stdout.decode().strip())
    else: 
        raise Exception('Curl command failed.')

# Compute the averages and standard deviations from the fetch_times
def compute_fetch(fetch_times):
    avgs, std_devs = [], []
    for three in fetch_times:
        mean = sum(three)/3
        summed = 0
        for fetch_time in three:
            summed += (fetch_time - mean)**2
        std_devs.append(sqrt(summed/3))
    avgs = [sum(i)/3 for i in fetch_times]
    return avgs, std_devs
    

def bufferbloat():
    if not os.path.exists(args.dir):
        os.makedirs(args.dir)
    os.system("sysctl -w net.ipv4.tcp_congestion_control=%s" % args.cong)
    topo = BBTopo()
    net = Mininet(topo=topo, host=CPULimitedHost, link=TCLink, controller=None)
    net.start()
    # This dumps the topology and how nodes are interconnected through
    # links.
    dumpNodeConnections(net.hosts)
    # This performs a basic all pairs ping test.
    net.pingAll()

    # Start monitoring the queue sizes.
    qmon = start_qmon(iface='s0-eth2',
                      outfile='%s/q.txt' % (args.dir))

    # Start iperf, ping train, webserver.
    start_iperf(net)
    start_ping(net)
    start_webserver(net)

    # Measure the time it takes to complete webpage transfer
    # from h1 to h2 3 times. 
    
    fetch_times = []
    start_time = time()

    while True:
        # Do the measurement 3 times every 5 seconds.
        three = []
        for i in range(3):
            three.append(measure_time(net))
        fetch_times.append(three)

        sleep(5)
        now = time()
        delta = now - start_time
        if delta > args.time:
            break
        print("%.1fs left..." % (args.time - delta))

    # Compute average (and standard deviation) of the fetch times. 

    result = compute_fetch(fetch_times)

    print('averages:', result[0])
    print('std_devs:', result[1])

    qmon.terminate()
    net.stop()
    # Ensure that all processes you create within Mininet are killed.
    # Sometimes they require manual killing.
    Popen("pgrep -f webserver.py | xargs kill -9", shell=True).wait()

if __name__ == "__main__":
    bufferbloat()

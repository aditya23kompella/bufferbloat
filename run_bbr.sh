#!/bin/bash

# Note: Mininet must be run as root. So invoke this shell script using sudo.

time=90
bwnet=1.5  # Bottleneck bandwidth in Mbps
delay=10   # One-way delay in ms

iperf_port=5001

for qsize in 20 100; do
    dir=bb-q-bbr$qsize

    # Run bufferbloat.py with appropriate args
    python3 bufferbloat.py --bw-net $bwnet --delay $delay --dir $dir --time $time --maxq $qsize --cong bbr

    # Generate plots
    python3 plot_queue.py -f $dir/q.txt -o bbr-buffer-q$qsize.png
    python3 plot_ping.py -f $dir/ping.txt -o bbr-rtt-q$qsize.png
done


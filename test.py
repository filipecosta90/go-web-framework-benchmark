#!/usr/bin/python3
# Go web frameworks test suite
# Python 3.X
# Version 0.1

import argparse
import datetime as dt
import os
import re
import subprocess
import sys
import time
import urllib.request

import cpuinfo
import humanize


def whereis(program):
    for path in os.environ.get('PATH', '').split(':'):
        if os.path.exists(os.path.join(path, program)) and \
                not os.path.isdir(os.path.join(path, program)):
            return os.path.join(path, program)
    return None


# Checks if system is configured for numa
def numa_capable():
    numa_capable = 0
    process = subprocess.Popen(['numactl --hardware | grep available'], shell=True, stdout=subprocess.PIPE)
    test_available = process.communicate()[0].decode()
    test_available = str.splitlines(test_available)
    if int(str.split(test_available[0])[1]) > 1:
        numa_capable = 1
    return numa_capable


# Check if system has the required utilities: wrk, numactl etc
def required_utilities(utility_list):
    required_utilities = 1
    for index in utility_list:
        if whereis(index) == None:
            print('Cannot locate ' + index + ' in path!')
            required_utilities = 0
        else:
            print('Found ' + index + ' at ' + whereis(index))
    return required_utilities


def wait_for_webserver(endpoint, retries=10):
    result = False
    while retries > 0:
        try:
            urllib.request.urlopen(endpoint).read()
            return True
        except urllib.error.URLError as e:
            print("...waiting for webserver to be ready")
            time.sleep(0.1)
            retries -= 1
    return result


def process_wrk_output(raw_output):
    decoded_raw = raw_output.decode()
    result = {"0.000000": None, "0.500000": None, "0.900000": None, "0.950000": None, "0.987500": None,
              "1.000000": None}
    for res in result.keys():
        result[res] = extract_latency(decoded_raw, res)
    result["rps"] = None
    rps_regex = re.search(
        '.*Requests\/sec:\s+(\d+.?\d*).*', decoded_raw)
    if rps_regex is not None:
        result["rps"] = float(rps_regex.group(1))

    print(decoded_raw)

    return result


def extract_latency(decoded_raw, latency_str):
    latency = None
    regex_lat = re.search(
        '\s+(\d+.\d+)\s+({})\s+(\d+)\s+(\d+.\d+)'.format(latency_str), decoded_raw)
    if regex_lat is not None:
        latency = float(regex_lat.group(1))
    else:
        regex_lat = re.search(
            '\s+(\d+.\d+)\s+({})\s+(\d+)\s+inf'.format(latency_str), decoded_raw)
        if regex_lat is not None:
            latency = float(regex_lat.group(1))
    return latency


def test_web_framework(wrk_full_path, server_bin_name, web_framework, processing_time_mock_ms, wrk_threads, connections,
                       max_rps,
                       duration_secs, endpoint, enable_cpu_affinity, taskset_web_framework_cpus_list,
                       taskset_wrk_cpus_list,
                       extra_wrk_args=[]):
    result = False
    result_data = None
    print("Testing web framework: {}, with processing time {} ms, and total of {} connections".format(web_framework,
                                                                                                      processing_time_mock_ms,
                                                                                                      connections))
    server_path = os.path.abspath("./{server_bin_name}".format(server_bin_name=server_bin_name))
    environ = os.environ.copy()
    stdoutPipe = subprocess.PIPE
    stderrPipe = subprocess.STDOUT
    stdinPipe = subprocess.PIPE

    web_framework_args = []
    if enable_cpu_affinity and taskset_web_framework_cpus_list is not None:
        web_framework_args += ["taskset", "-c", ",".join(["{}".format(x) for x in taskset_web_framework_cpus_list])]
    web_framework_args += [server_path, web_framework, "{}".format(processing_time_mock_ms)]

    wrk_args = []
    if enable_cpu_affinity and taskset_wrk_cpus_list is not None:
        wrk_args += ["taskset", "-c", ",".join(["{}".format(x) for x in taskset_wrk_cpus_list])]
    wrk_args += [wrk_full_path, "-t{}".format(wrk_threads), "-c{}".format(connections), "-R{}".format(max_rps),
                 "-d{}s".format(duration_secs), "--latency", endpoint]
    wrk_args += extra_wrk_args

    options = {
        'stderr': stderrPipe,
        'stdin': stdinPipe,
        'stdout': stdoutPipe,
        'env': environ,
    }

    web_framework_process = subprocess.Popen(args=web_framework_args, **options)
    print("Waiting for web framework to be ready...")
    wait_for_webserver(endpoint)
    print("Ready to benchmark...")

    if web_framework_process.poll() is None:
        print("web framework process is alive")

    wrk_process = subprocess.Popen(args=wrk_args, **options)
    if wrk_process.poll() is None:
        print("wrk process is alive")

    wrk_output = wrk_process.communicate()[0]
    result_data = process_wrk_output(wrk_output)

    try:
        print("Terminating {} process".format(web_framework))
        web_framework_process.terminate()
        web_framework_process.wait()
        print("{} process exited successfully".format(web_framework))

    except OSError as e:
        print('OSError caught while waiting for {0} process to end: {1}'.format(web_framework, e.__str__()))
        pass

    return result, result_data


# Main Function
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='Go Web framework benchmark suite.',
                                     formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    parser.add_argument('--test-frameworks', type=str, help="web frameworks being tested (comma separated)",
                        default="default,atreugo,beego,bone,chi,denco,echov3,fasthttp-rawfasthttp-routing,fasthttp/router,fasthttprouter,fastrouter,fiber,fresh,gear,gin,goji,gojsonrest,gongular,gorestful,gorilla,gorouter,gorouterfasthttp,go-ozzo,gowww,gramework,httprouterhttptreemux,lars,lion,martini,muxie,negroni,neo,pat,pure,r2router,tango,tiger,tinyrouter,traffic,violetear,vulcan,webgo")
    parser.add_argument('--web-framework-max-cpus', type=int, default=0,
                        help='web frameworks max cpus. If set to 0 will auto adjust based on --wrk-max-cpus and total cores of the machine')
    parser.add_argument('--wrk-max-cpus', type=int, default=0,
                        help='wrk max cpus. If set to 0 will auto adjust based on --web-framework-max-cpus and total cores of the machine')
    parser.add_argument('--test-duration-secs', type=int, default=30,
                        help='test duration secs')
    parser.add_argument('--sleep-between-runs-secs', type=int, default=15,
                        help='sleep between runs')
    parser.add_argument('--enable-cpu-affinity', default=False, action='store_true')
    parser.add_argument('--wrk-connections', type=str, default="1,100,500,5000",
                        help='different wrk total connections to simulate')
    parser.add_argument('--web-framework-processing-time-ms', type=str, default="0,10,30,100,500,-1",
                        help='web framework processing times to simulate. -1 is a special case for cpu bound testing ( via pow )')
    parser.add_argument('--server-bin-name', type=str, default="gowebbenchmark")
    parser.add_argument('--endpoint', type=str, default="http://127.0.0.1:8080/hello")
    parser.add_argument('--stress-rps', type=int, default=5000000,
                        help="RPS limit that is not supposed to be achievable. All frameworks should achieve it\'s stress point bellow this value")

    args = parser.parse_args()
    info = cpuinfo.get_cpu_info()
    total_cores = info['count']
    web_framework_max_procs = args.web_framework_max_cpus
    wrk_max_procs = args.wrk_max_cpus
    if web_framework_max_procs == 0 and wrk_max_procs == 0:
        web_framework_max_procs = total_cores // 2
        wrk_max_procs = total_cores // 2
    elif web_framework_max_procs == 0 and wrk_max_procs != 0:
        web_framework_max_procs = total_cores - wrk_max_procs
    elif web_framework_max_procs != 0 and wrk_max_procs == 0:
        wrk_max_procs = total_cores - web_framework_max_procs

    total_benchmark_cpus = wrk_max_procs + web_framework_max_procs
    benchmark_cpus_list = range(0, total_benchmark_cpus)
    web_framework_cpus_list = benchmark_cpus_list[0:web_framework_max_procs] if args.enable_cpu_affinity else []
    wrk_cpus_list = benchmark_cpus_list[
                    web_framework_max_procs:total_benchmark_cpus] if args.enable_cpu_affinity else []

    print("Using a total of {} CPUs out of the machine {} CPUs. cpus list: {}".format(total_benchmark_cpus, total_cores,
                                                                                      " ".join(["{}".format(x) for x in
                                                                                                benchmark_cpus_list])))

    print("Using {} CPUs for web-frameworks. cpus list: {}".format(web_framework_max_procs, " ".join(
        ["{}".format(x) for x in web_framework_cpus_list])))
    print(
        "Using {} CPUs for wrk. cpus list: {}".format(wrk_max_procs, " ".join(["{}".format(x) for x in wrk_cpus_list])))

    os.environ["GOMAXPROCS"] = "{}".format(web_framework_max_procs)

    test_connections = [int(x) for x in args.wrk_connections.split(",")]
    processing_times_ms = [int(x) for x in args.web_framework_processing_time_ms.split(",")]
    web_frameworks = args.test_frameworks.split(",")

    total_time = len(web_frameworks) * len(processing_times_ms) * len(test_connections) * (
            args.test_duration_secs + args.sleep_between_runs_secs)
    print("Testing {} distinct frameworks".format(len(web_frameworks)))
    print("Total expected benchmark time {}".format(humanize.naturaldelta(dt.timedelta(seconds=total_time))))

    required_utilities_list = ['numactl', 'wrk']
    if args.enable_cpu_affinity:
        required_utilities_list.append('taskset')

    if required_utilities(required_utilities_list) == 0:
        print('Utilities Missing! Exiting..')
        sys.exit(1)

    if numa_capable() == 1:
        print(
            "WARNING!!! Machine is NUMA capable, meaning that memory is configured with a NUMA layout (having local and remote memory)."
            " You should adjust CPU pinning in accordance. Don\'t this benchmark as is for NUMA setups!!")

    wrk_full_path = whereis("wrk")
    # for web_framework in web_frameworks:
    for web_framework in ["chi"]:
        for test_connection in test_connections:
            for processing_time_ms in processing_times_ms:
                test_wrk_max_procs = wrk_max_procs
                if test_connection < wrk_max_procs:
                    print("Setting threads to {}, given than number of connections ({}) must be >= threads ({})".format(
                        test_connection, test_connection, wrk_max_procs))
                    test_wrk_max_procs = test_connection
                status, result_data = test_web_framework(wrk_full_path, args.server_bin_name, web_framework,
                                                         processing_time_ms, test_wrk_max_procs, test_connection,
                                                         args.stress_rps,
                                                         args.test_duration_secs,
                                                         args.endpoint, args.enable_cpu_affinity,
                                                         web_framework_cpus_list, wrk_cpus_list)
                print(result_data)
                time.sleep(args.sleep_between_runs_secs)

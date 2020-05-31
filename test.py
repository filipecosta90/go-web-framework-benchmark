#!/usr/bin/python3
# Go web frameworks test suite
# Python 3.X
# Version 0.1

import argparse
import datetime as dt
import json
import os
import re
import subprocess
import sys
import time
import urllib.request

import cpuinfo
import humanize
from tqdm import tqdm


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
def required_utilities(utility_list, debug):
    result = 1
    for index in utility_list:
        if whereis(index) == None:
            if debug:
                print('Cannot locate ' + index + ' in path!')
            result = 0
        elif debug:
            print('Found ' + index + ' at ' + whereis(index))
    return result


def wait_for_webserver(endpoint, debug, retries=10):
    result = False
    while retries > 0:
        try:
            urllib.request.urlopen(endpoint).read()
            return True
        except urllib.error.URLError as e:
            if debug:
                print("...waiting for webserver to be ready")
            time.sleep(0.1)
            retries -= 1
    return result


def get_raw_uncorrected_latency_histogram(decoded_raw):
    uncorrected_lines = []
    within_line_interval = False
    for line in decoded_raw.split("\n"):
        # print(line)
        if "Latency Distribution (HdrHistogram - Uncorrected Latency (measured without taking delayed starts into account))" in line:
            within_line_interval = True
        if "----------------------------------------------------------" in line:
            within_line_interval = False
        if within_line_interval:
            uncorrected_lines.append(line)

    return uncorrected_lines


def get_raw_corrected_latency_histogram(decoded_raw):
    corrected_lines = []
    within_line_interval = False
    for line in decoded_raw.split("\n"):
        if "Latency Distribution (HdrHistogram - Recorded Latency)" in line:
            within_line_interval = True
        if "----------------------------------------------------------" in line:
            within_line_interval = False
        if within_line_interval:
            corrected_lines.append(line)

    return corrected_lines


def process_wrk_output(raw_output):
    decoded_raw = raw_output.decode()
    result = {"corrected": {}, "uncorrected": {}}
    uncorrected_latency_histogram = get_raw_uncorrected_latency_histogram(decoded_raw)
    corrected_latency_histogram = get_raw_corrected_latency_histogram(decoded_raw)
    for line in uncorrected_latency_histogram:
        quantile, latency = extract_latency(line)
        if quantile is not None:
            result["uncorrected"][quantile] = latency
    for line in corrected_latency_histogram:
        quantile, latency = extract_latency(line)
        if quantile is not None:
            result["corrected"][quantile] = latency
    result["rps"] = None
    rps_regex = re.search(
        '.*Requests\/sec:\s+(\d+.?\d*).*', decoded_raw)
    if rps_regex is not None:
        result["rps"] = float(rps_regex.group(1))

    return result


def extract_latency(decoded_raw):
    latency = None
    quantile = None
    regex_lat = re.search(
        '\s+(\d+.\d+)\s+(\d+.\d+)\s+(\d+)\s+(\d+.\d+)', decoded_raw)
    if regex_lat is not None:
        latency = float(regex_lat.group(1))
        quantile = float(regex_lat.group(2))
    else:
        regex_lat = re.search(
            '\s+(\d+.\d+)\s+(\d+.\d+)\s+(\d+)\s+inf', decoded_raw)
        if regex_lat is not None:
            latency = float(regex_lat.group(1))
            quantile = float(regex_lat.group(2))
    return quantile, latency


def test_web_framework(wrk_full_path, server_bin_name, web_framework, processing_time_mock_ms, wrk_threads, connections,
                       max_rps,
                       duration_secs, endpoint, enable_cpu_affinity, taskset_web_framework_cpus_list,
                       taskset_wrk_cpus_list, debug, pipeline_size,
                       extra_wrk_args=[]):
    result = False
    result_data = None
    if debug:
        print("Testing web framework: {}, with processing time {} ms, and total of {} connections".format(web_framework,
                                                                                                          processing_time_mock_ms,
                                                                                                          connections))
    server_path = os.path.abspath("./{server_bin_name}".format(server_bin_name=server_bin_name))
    lua_path = os.path.abspath("./pipeline.lua")
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
                 "-d{}s".format(duration_secs), "--u_latency", endpoint, "-s", lua_path, "--", "/hello",
                 "{}".format(pipeline_size)]
    wrk_args += extra_wrk_args

    options = {
        'stderr': stderrPipe,
        'stdin': stdinPipe,
        'stdout': stdoutPipe,
        'env': environ,
    }

    web_framework_process = subprocess.Popen(args=web_framework_args, **options)
    if debug:
        print("Waiting for web framework to be ready...")
    wait_for_webserver(endpoint, debug)
    if debug:
        print("Ready to benchmark...")

    if debug:
        if web_framework_process.poll() is None:
            print("web framework process is alive")

    wrk_process = subprocess.Popen(args=wrk_args, **options)
    if debug:
        if wrk_process.poll() is None:
            print("wrk process is alive")
    if wrk_process.poll() is None:
        wrk_output = wrk_process.communicate()[0]
        result_data = process_wrk_output(wrk_output)

    try:
        if debug:
            print("Terminating {} process".format(web_framework))
        web_framework_process.terminate()
        web_framework_process.wait()
        if debug:
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
    parser.add_argument('--disable-test-pipeline', default=False, action='store_true')
    parser.add_argument('--debug', default=False, action='store_true')
    parser.add_argument('--wrk-connections', type=str, default="1,100,500,5000",
                        help='different wrk total connections to simulate')
    parser.add_argument('--web-framework-processing-time-ms', type=str, default="0,10,30,100,500,-1",
                        help='web framework processing times to simulate. -1 is a special case for cpu bound testing ( via pow )')
    parser.add_argument('--server-bin-name', type=str, default="gowebbenchmark")
    parser.add_argument('--endpoint', type=str, default="http://127.0.0.1:8080")
    parser.add_argument('--output-file', type=str, default="results.json")
    parser.add_argument('--pipeline-sizes', type=str, default="1,5,10,20",
                        help='different pipeline sizes to test for')
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
    benchmark_cpus_list = list(range(0, total_benchmark_cpus))
    web_framework_cpus_list = list(benchmark_cpus_list[0:web_framework_max_procs]) if args.enable_cpu_affinity else []
    wrk_cpus_list = list(benchmark_cpus_list[
                         web_framework_max_procs:total_benchmark_cpus]) if args.enable_cpu_affinity else []

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
    test_pipelines = [int(x) for x in args.pipeline_sizes.split(",")]
    web_frameworks = args.test_frameworks.split(",")
    total_tests = len(web_frameworks) * len(processing_times_ms) * len(test_connections) * len(test_pipelines)
    total_time = total_tests * (
            args.test_duration_secs + args.sleep_between_runs_secs)
    print("Testing {} distinct frameworks".format(len(web_frameworks)))
    print("Total expected benchmark time {}".format(humanize.naturaldelta(dt.timedelta(seconds=total_time))))

    required_utilities_list = ['numactl', 'wrk']
    if args.enable_cpu_affinity:
        required_utilities_list.append('taskset')

    if required_utilities(required_utilities_list, args.debug) == 0:
        print('Utilities Missing! Exiting..')
        sys.exit(1)

    if numa_capable() == 1:
        print(
            "WARNING!!! Machine is NUMA capable, meaning that memory is configured with a NUMA layout (having local and remote memory)."
            " You should adjust CPU pinning in accordance. Don\'t this benchmark as is for NUMA setups!!")

    wrk_full_path = whereis("wrk")

    overall_results = {"machine_info": info, "wrk_max_procs": wrk_max_procs, "wrk_cpus_list": wrk_cpus_list,
                       "web_framework_max_procs": web_framework_max_procs,
                       "web_framework_cpus_list": web_framework_cpus_list}
    progress = tqdm(unit="tests", total=total_tests)
    for web_framework in web_frameworks:
        overall_results[web_framework] = {}
        for test_connection in test_connections:
            connection_key = "connections-{}".format(test_connection)
            overall_results[web_framework][connection_key] = {}
            for processing_time_ms in processing_times_ms:
                processing_time_key = "mocked-processing-time-{}-ms".format(processing_time_ms)
                test_wrk_max_procs = wrk_max_procs
                if test_connection < wrk_max_procs:
                    print("Setting threads to {}, given than number of connections ({}) must be >= threads ({})".format(
                        test_connection, test_connection, wrk_max_procs))
                    test_wrk_max_procs = test_connection
                overall_results[web_framework][connection_key][processing_time_key] = {}
                for pipeline in test_pipelines:
                    pipeline_key = "pipeline-{}".format(pipeline)
                    overall_results[web_framework][connection_key][processing_time_key][pipeline_key] = {}
                    status, result_data = test_web_framework(wrk_full_path, args.server_bin_name, web_framework,
                                                             processing_time_ms, test_wrk_max_procs, test_connection,
                                                             args.stress_rps,
                                                             args.test_duration_secs,
                                                             args.endpoint, args.enable_cpu_affinity,
                                                             web_framework_cpus_list, wrk_cpus_list, args.debug,
                                                             pipeline)
                    overall_results[web_framework][connection_key][processing_time_key][pipeline_key] = result_data
                    progress.update()
                    q50 = None
                    if 0.5 in result_data["uncorrected"]:
                        q50 = result_data["uncorrected"][0.5]
                    print(
                        "Framework {}, connections {}, mocked processing time {} ms, pipeline {}. RPS {} rps. q50 {} ms".format(
                            web_framework,
                            test_connection,
                            processing_time_ms,
                            pipeline,
                            result_data["rps"], q50))
                    time.sleep(args.sleep_between_runs_secs)
    progress.close()

    with open(args.output_file, "w") as json_file:
        json.dump(overall_results, json_file)

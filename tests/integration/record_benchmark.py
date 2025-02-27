#!/usr/bin/env python3

import argparse
import logging
import boto3
import json
import os
import time
from decimal import Decimal

logging.basicConfig(level=logging.INFO)

parser = argparse.ArgumentParser(
    description="Script for saving container benchmark results")
parser.add_argument("--container",
                    required=True,
                    type=str,
                    help="The container to run the job")
parser.add_argument("--template",
                    required=True,
                    type=str,
                    help="The template json string")
parser.add_argument("--instance",
                    required=True,
                    type=str,
                    help="The current instance name")
parser.add_argument("--record",
                    choices=["table"],
                    required=False,
                    type=str,
                    help="Where to record to")
parser.add_argument("--job", required=True, type=str, help="The job string")
args = parser.parse_args()

data = {}


class Benchmark:

    def __init__(self, dyn_resource):
        self.dyn_resource = dyn_resource
        self.table = dyn_resource.Table("RubikonBenchmarks")
        self.table.load()

    def add_benchmark(self):
        self.table.put_item(Item=data)


def record_table():
    table = boto3.resource("dynamodb").Table("RubikonBenchmarks")
    table.put_item(Item=data)


def data_basic():
    data["modelServer"] = "DJLServing"
    data["service"] = "ec2"
    data["Timestamp"] = Decimal(time.time())

    data["instance"] = args.instance

    container = args.container
    data["container"] = container
    if container.startswith("deepjavalibrary/djl-serving:"):
        container = container[len("deepjavalibrary/djl-serving:"):]
        split = container.split("-", 1)
        data["djlVersion"] = split[0]
        if len(split) > 1:
            data["image"] = split[1]
        else:
            data["image"] = "cpu"


def data_from_client():
    with open("benchmark.log", "r") as f:
        for line in f.readlines():
            line = line.strip()
            if "Total time:" in line:
                data["totalTime"] = Decimal(line.split(" ")[2])
            if "error rate:" in line:
                data["errorRate"] = Decimal(line.split(" ")[-1])
            if "Concurrent clients:" in line:
                data["concurrency"] = int(line.split(" ")[2])
            if "Total requests:" in line:
                data["requests"] = int(line.split(" ")[2])
            if "TPS:" in line:
                data["tps"] = Decimal(line.split(" ")[1].split("/")[0])
            if "Average Latency:" in line:
                data["avgLatency"] = Decimal(line.split(" ")[2])
            if "P50:" in line:
                data["P50"] = Decimal(line.split(" ")[1])
            if "P90:" in line:
                data["P90"] = Decimal(line.split(" ")[1])
            if "P99:" in line:
                data["P99"] = Decimal(line.split(" ")[1])
            if "totalTime" in data and "requests" in data:
                data["throughput"] = data["requests"] / data["totalTime"]


def data_from_files():
    with open("models/test/serving.properties", "r") as f:
        properties = {}
        for line in f.readlines():
            line = line.strip()
            if line[0] == "#":
                continue
            if "=" in line:
                split = line.split("=", 1)
                k = split[0].replace(".", "-")
                v = split[1].replace(".", "-")
                properties[k] = v
        data["serving_properties"] = properties

        # Standard properties
        if "option-model_id" in properties:
            data["modelId"] = properties["option-model_id"]
        if "option-tensor_parallel_degree" in properties:
            data["tensorParallel"] = properties[
                "option-tensor_parallel_degree"]

    if os.path.isfile("models/test/requirements.txt"):
        with open("models/test/requirements.txt", "r") as f:
            req = {}
            for line in f.readlines():
                line = line.strip()
                if line[0] == "#":
                    continue
                if "=" in line:
                    split = line.split("=", 1)
                    req[split[0]] = split[1]
            data["requirements_txt"] = req


def data_from_template():
    with open(args.template, "r") as f:
        template = json.load(f)
        job_template = template[args.job]
        data["awscurl"] = bytes.fromhex(
            job_template['awscurl']).decode("utf-8")
        if "info" in job_template:
            for line in job_template["info"]:
                split = line.split("=", 1)
                data[split[0]] = split[1]
            return True
        else:
            return False


if __name__ == "__main__":
    data_from_template()
    data_basic()
    data_from_client()
    data_from_files()

    if "errorRate" not in data or data["errorRate"] == 100:
        print("Not recording failed benchmark")
        print(data)
    else:
        if args.record == "table":
            record_table()
        else:
            print(data)

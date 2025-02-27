#!/usr/bin/env python
#
# Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License"). You may not use this file
# except in compliance with the License. A copy of the License is located at
#
# http://aws.amazon.com/apache2.0/
#
# or in the "LICENSE.txt" file accompanying this file. This file is distributed on an "AS IS"
# BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, express or implied. See the License for
# the specific language governing permissions and limitations under the License.
import logging
import tensorrt_llm_toolkit
from djl_python.rolling_batch.rolling_batch import RollingBatch, stop_on_any_exception, Token


class TRTLLMRollingBatch(RollingBatch):

    def __init__(self, model_id_or_path, device, properties, **kwargs):
        """
        Initializes the TRTLLMRollingBatch.
        :param model_id_or_path: model id or path
        :param properties: other properties of the model, such as decoder strategy
        """
        super().__init__(**kwargs)
        if properties.get("mpi_mode") != "true":
            raise AssertionError(
                f"Need mpi_mode to start tensorrt llm RollingBatcher")
        self.model = tensorrt_llm_toolkit.init_inference(
            model_id_or_path, **kwargs)
        self.request_cache = {}

    def reset(self):
        """
        Stops all current requests and resets state of rolling batch portion of handler
        """
        # TODO: delete running requests of Triton will cause backend crashes
        # bare with memory leaks of failed requests
        self.request_cache.clear()
        super().reset()

    def translate_triton_params(self, parameters):
        if "request_output_len" not in parameters.keys():
            parameters["request_output_len"] = parameters.pop(
                "max_new_tokens", 128)
        if "top_k" in parameters.keys():
            parameters["runtime_top_k"] = parameters.pop("top_k")
        if "top_p" in parameters.keys():
            parameters["runtime_top_p"] = parameters.pop("top_p")
        if "seed" in parameters.keys():
            parameters["random_seed"] = int(parameters.pop("seed"))
        if parameters.pop("do_sample", False):
            parameters["runtime_top_k"] = parameters.get("runtime_top_k", 5)
            parameters["runtime_top_p"] = parameters.get("runtime_top_p", 0.85)
            parameters["temperature"] = parameters.get("temperature", 0.8)
        parameters["streaming"] = parameters.get("streaming", True)
        return parameters

    @stop_on_any_exception
    def inference(self, input_data, parameters):
        batch_size = len(input_data)
        # add pending requests to active requests list
        new_requests = self.get_new_requests(input_data, parameters,
                                             batch_size)
        # step 0: register new active requests
        for request in new_requests:
            param = self.translate_triton_params(request.parameters)
            output_len = param["request_output_len"]
            response = self.model.generate(request.input_text, **param)
            self.request_cache[request.id] = {
                "response": response,
                "out_length": output_len,
                "cumulative_logprob": 0
            }

        # step 1: loop the active requests to send result
        for request in self.active_requests:
            trt_resp = self.request_cache[request.id]["response"]
            generation = trt_resp.fetch()
            log_prob = generation.cum_logprob - self.request_cache[
                request.id]["cumulative_logprob"]
            self.request_cache[
                request.id]["cumulative_logprob"] = generation.cum_logprob
            token = Token(generation.token_id, generation.token_text, log_prob,
                          None)
            if generation.finished:
                finish_reason = "eos_token" if generation.seq_length < self.request_cache[
                    request.id]["out_length"] else "length"
                request.set_next_token(token, self.output_formatter,
                                       generation.finished, finish_reason)
                self.request_cache.pop(request.id)
            else:
                request.set_next_token(token, self.output_formatter,
                                       generation.finished)

        return self.postprocess_results()

    def preprocess_requests(self, requests):
        raise NotImplementedError(
            "Not implemented for tensorrtllm rolling batcher")

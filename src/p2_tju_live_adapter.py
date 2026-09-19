"""Bounded P2 stage calls and image preparation over a selected provider.

Historical class name remains compatible. Provider imports are lazy; injection,
prompt separation and per-stage token/time limits remain explicit.
"""

MAX_P2_OUTPUT_TOKENS = 2048
# The formal workload response now includes a complete ledger and provenance.
# Live multi-section estimates were cut at 768 with finish_reason=length.
MAX_WORKLOAD_OUTPUT_TOKENS = 2048
P2_REQUEST_TIMEOUT_SECONDS = 120


class TJUP2CallAdapter(object):
    """P2 live caller：agent_caller(system_prompt, user_prompt) -> str。"""

    # 2026-09-07: the selected ``tju-llm`` deployment and this exact
    # OpenAI-compatible Data-URI path were verified by the user from the same
    # environment as the live app (HTTP 200; text and a blue circle read
    # correctly).  This is an interface capability flag, not a claim that
    # every task or timetable screenshot will be recognized perfectly.
    supports_image_inputs = True

    def __init__(
        self, call_function=None, message_call_function=None,
        supports_image_inputs=None,
        recognition_tools=None,
    ):
        self._call_function = call_function
        self._message_call_function = message_call_function
        # Verified on TJU only. Explicit False preserves the content channel;
        # injected/other-provider callers retain their existing capabilities.
        if recognition_tools is None:
            from src.llm_provider import provider_name
            recognition_tools=(provider_name()=='tju' and call_function is None and message_call_function is None)
        self.recognition_tools = recognition_tools
        if supports_image_inputs is not None:
            self.supports_image_inputs = bool(supports_image_inputs)

    def agent_caller(self, system_prompt, user_prompt):
        if self.recognition_tools and any(s in system_prompt for s in ('campusflow.material-text.v2','campusflow.material-serialization.v1')):
            from src.material_function_transport import call_recognition
            from src.llm_provider import call_llm_messages
            return call_recognition(system_prompt,user_prompt,self._message_call_function or call_llm_messages,
                P2_REQUEST_TIMEOUT_SECONDS,MAX_P2_OUTPUT_TOKENS)
        if self._call_function is None:
            from src.llm_provider import call_llm as call_tju_llm
            call_function = call_tju_llm
        else:
            call_function = self._call_function

        return call_function(
            user_prompt,
            system_prompt=system_prompt,
            temperature=0,
            max_tokens=MAX_P2_OUTPUT_TOKENS,
            timeout=P2_REQUEST_TIMEOUT_SECONDS,
        )

    def task_estimation_caller(
        self, system_prompt, user_prompt, image_mime=None, image_bytes=None
    ):
        """Text estimate caller plus an explicitly gated multimodal boundary."""
        if image_bytes is None:
            return self.agent_caller(system_prompt, user_prompt)
        if not self.supports_image_inputs:
            from src.tju_llm_client import TJUClientError
            raise TJUClientError("当前学校模型服务尚未确认支持图片理解。")
        from src.task_estimation import TaskEstimateMaterial
        material = TaskEstimateMaterial(
            image_name="task-image",
            image_mime=image_mime,
            image_bytes=image_bytes,
        )
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": material.image_content(user_prompt)},
        ]
        if self._message_call_function is None:
            from src.llm_provider import call_llm_messages as call_tju_llm_messages
            message_call = call_tju_llm_messages
        else:
            message_call = self._message_call_function
        if self.recognition_tools and 'campusflow.material-text.v2' in system_prompt:
            from src.material_function_transport import call_recognition
            return call_recognition(system_prompt,user_prompt,message_call,
                P2_REQUEST_TIMEOUT_SECONDS,MAX_P2_OUTPUT_TOKENS,content=messages[1]['content'])
        return message_call(
            messages,
            temperature=0,
            max_tokens=MAX_P2_OUTPUT_TOKENS,
            timeout=P2_REQUEST_TIMEOUT_SECONDS,
        )

    def workload_estimation_caller(self, system_prompt, user_prompt):
        """Bounded text-only estimate including its complete formal ledger."""
        call_function = self._call_function
        if call_function is None:
            from src.llm_provider import call_llm as call_tju_llm
            call_function = call_tju_llm
        return call_function(user_prompt, system_prompt=system_prompt,
            temperature=0, max_tokens=MAX_WORKLOAD_OUTPUT_TOKENS, timeout=30)

    def material_images_caller(self, system_prompt, user_prompt, images):
        """Finite PDF pages in one user message via the existing live client."""
        from src.file_material import MAX_VISION_PAGES, MAX_VISION_BYTES
        from src.task_estimation import TaskEstimateMaterial
        if (not self.supports_image_inputs or not 1 <= len(images) <= MAX_VISION_PAGES
                or sum(len(data) for _, data in images) > MAX_VISION_BYTES):
            raise ValueError('document image limit exceeded')
        content = [{'type': 'text', 'text': user_prompt}]
        for mime, data in images:
            material = TaskEstimateMaterial(image_name='document-page', image_mime=mime, image_bytes=data)
            content.extend(material.image_content('')[1:])
        message_call = self._message_call_function
        if message_call is None:
            from src.llm_provider import call_llm_messages as call_tju_llm_messages
            message_call = call_tju_llm_messages
        if self.recognition_tools and 'campusflow.material-text.v2' in system_prompt:
            from src.material_function_transport import call_recognition
            return call_recognition(system_prompt,user_prompt,message_call,
                P2_REQUEST_TIMEOUT_SECONDS,MAX_P2_OUTPUT_TOKENS,content=content)
        return message_call([{'role':'system','content':system_prompt},
                             {'role':'user','content':content}], temperature=0,
                            max_tokens=MAX_P2_OUTPUT_TOKENS, timeout=P2_REQUEST_TIMEOUT_SECONDS)

package com.eventbreaker.analyzer.model;

import java.util.List;

public record MethodCallInfo(
        String expression,
        String scope,
        String methodName,
        List<String> arguments,
        List<MethodCallInfo> nestedCalls
) {
}
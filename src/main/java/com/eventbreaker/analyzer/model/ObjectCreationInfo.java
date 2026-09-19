package com.eventbreaker.analyzer.model;

import java.util.List;

public record ObjectCreationInfo(
        String type,
        String expression,
        List<String> arguments,
        List<MethodCallInfo> nestedCalls
) {
}
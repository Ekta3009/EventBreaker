package com.eventbreaker.analyzer.model;

import java.util.List;

public record ConsumerAnalysis(
        String className,
        String methodName,
        String eventType,
        List<String> parameters,
        List<DependencyInfo> dependencies,
        List<MethodCallInfo> methodCalls,
        List<ObjectCreationInfo> objectCreations,
        List<AssignmentInfo> assignments,
        List<VariableInitializationInfo> variableInitializations
) {
}
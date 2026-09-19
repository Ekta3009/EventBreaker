package com.eventbreaker.analyzer.model;

public record VariableInitializationInfo(
        String variableName,
        String variableType,
        String initializer,
        MethodCallInfo initializerMethodCall,
        ObjectCreationInfo initializerObjectCreation
) {
}
package com.eventbreaker.analyzer.model;

public record AssignmentInfo(
        String target,
        String value,
        String expression
) {
}
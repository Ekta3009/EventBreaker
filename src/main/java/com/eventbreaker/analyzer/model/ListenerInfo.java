package com.eventbreaker.analyzer.model;

/** One listener method found by a repository scan. */
public record ListenerInfo(
        String file,
        String className,
        String methodName,
        String eventType
) {
}

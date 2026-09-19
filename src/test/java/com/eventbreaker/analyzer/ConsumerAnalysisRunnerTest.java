package com.eventbreaker.analyzer;

import com.eventbreaker.analyzer.model.ConsumerAnalysis;
import org.junit.jupiter.api.Test;

import java.nio.file.Path;
import java.util.List;
import java.util.Set;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

class ConsumerAnalysisRunnerTest {

    @Test
    void analyzesAllConsumerFixturesInDirectory()
            throws Exception {

        ConsumerAnalysisRunner runner =
                new ConsumerAnalysisRunner();

        List<ConsumerAnalysis> analyses =
                runner.analyzeDirectory(
                        Path.of(
                                "src/test/resources/consumers"
                        )
                );

        assertEquals(
                6,
                analyses.size()
        );

        Set<String> consumerNames =
                analyses.stream()
                        .map(ConsumerAnalysis::className)
                        .collect(java.util.stream.Collectors.toSet());

        assertTrue(
                consumerNames.contains("SimpleConsumer")
        );

        assertTrue(
                consumerNames.contains("NotificationConsumer")
        );

        assertTrue(
                consumerNames.contains("DeepNestedConsumer")
        );

        assertTrue(
                consumerNames.contains("ConditionalConsumer")
        );

        assertTrue(
                consumerNames.contains("ComplexConsumer")
        );

        assertTrue(
                consumerNames.contains("OrderConsumer")
        );
    }
}
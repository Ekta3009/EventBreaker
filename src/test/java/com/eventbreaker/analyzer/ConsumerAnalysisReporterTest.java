package com.eventbreaker.analyzer;

import com.eventbreaker.analyzer.model.AssignmentInfo;
import com.eventbreaker.analyzer.model.ConsumerAnalysis;
import com.eventbreaker.analyzer.model.DependencyInfo;
import com.eventbreaker.analyzer.model.MethodCallInfo;
import com.eventbreaker.analyzer.model.ObjectCreationInfo;
import com.eventbreaker.analyzer.model.VariableInitializationInfo;
import org.junit.jupiter.api.Test;

import java.util.List;

import static org.junit.jupiter.api.Assertions.assertTrue;

class ConsumerAnalysisReporterTest {

    @Test
    void formatsConsumerMetadata() {

        ConsumerAnalysis analysis =
                new ConsumerAnalysis(
                        "TestConsumer",
                        "handle",
                        "TestEvent",
                        List.of("TestEvent event"),
                        List.of(
                                new DependencyInfo(
                                        "repository",
                                        "TestRepository"
                                )
                        ),
                        List.of(),
                        List.of(),
                        List.of(),
                        List.of()
                );

        ConsumerAnalysisReporter reporter =
                new ConsumerAnalysisReporter();

        String report =
                reporter.format(analysis);

        assertTrue(report.contains(
                "TestConsumer.handle"
        ));

        assertTrue(report.contains(
                "TestEvent"
        ));

        assertTrue(report.contains(
                "TestEvent event"
        ));

        assertTrue(report.contains(
                "repository → TestRepository"
        ));
    }

    @Test
    void formatsNestedMethodCallHierarchy() {

        MethodCallInfo nestedArgument =
                new MethodCallInfo(
                        "event.getCustomer().getId()",
                        "event.getCustomer()",
                        "getId",
                        List.of(),
                        List.of(
                                new MethodCallInfo(
                                        "event.getCustomer()",
                                        "event",
                                        "getCustomer",
                                        List.of(),
                                        List.of()
                                )
                        )
                );

        MethodCallInfo nestedCall =
                new MethodCallInfo(
                        "event.getOrder().withCustomer(event.getCustomer().getId())",
                        "event.getOrder()",
                        "withCustomer",
                        List.of(
                                "event.getCustomer().getId()"
                        ),
                        List.of(
                                new MethodCallInfo(
                                        "event.getOrder()",
                                        "event",
                                        "getOrder",
                                        List.of(),
                                        List.of()
                                ),
                                nestedArgument
                        )
                );

        MethodCallInfo rootCall =
                new MethodCallInfo(
                        "orderRepository.save(event.getOrder().withCustomer(event.getCustomer().getId()))",
                        "orderRepository",
                        "save",
                        List.of(
                                "event.getOrder().withCustomer(event.getCustomer().getId())"
                        ),
                        List.of(nestedCall)
                );

        ConsumerAnalysis analysis =
                new ConsumerAnalysis(
                        "DeepNestedConsumer",
                        "handle",
                        "OrderUpdated",
                        List.of("OrderUpdated event"),
                        List.of(
                                new DependencyInfo(
                                        "orderRepository",
                                        "OrderRepository"
                                )
                        ),
                        List.of(rootCall),
                        List.of(),
                        List.of(),
                        List.of()
                );

        ConsumerAnalysisReporter reporter =
                new ConsumerAnalysisReporter();

        String report =
                reporter.format(analysis);

        assertTrue(report.contains(
                "orderRepository.save(event.getOrder().withCustomer(event.getCustomer().getId()))"
        ));

        assertTrue(report.contains(
                "event.getOrder().withCustomer(event.getCustomer().getId())"
        ));

        assertTrue(report.contains(
                "├── event.getOrder()"
        ));

        assertTrue(report.contains(
                "└── event.getCustomer().getId()"
        ));

        assertTrue(report.contains(
                "└── event.getCustomer()"
        ));
    }
}
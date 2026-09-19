package com.eventbreaker.analyzer;

import com.eventbreaker.analyzer.model.ConsumerAnalysis;
import com.eventbreaker.analyzer.model.DependencyInfo;
import com.eventbreaker.analyzer.model.MethodCallInfo;
import org.junit.jupiter.api.Test;

import java.nio.file.Path;

import static org.junit.jupiter.api.Assertions.*;

class ConsumerAnalyzerTest {

    private final ConsumerAnalyzer analyzer =
            new ConsumerAnalyzer();

    @Test
    void analyzesSimpleConsumer() throws Exception {

        ConsumerAnalysis analysis =
                analyzer.analyze(
                        Path.of(
                                "src/test/resources/consumers/SimpleConsumer.java"
                        )
                );

        assertEquals(
                "SimpleConsumer",
                analysis.className()
        );

        assertEquals(
                "handle",
                analysis.methodName()
        );

        assertEquals(
                "InventoryUpdated",
                analysis.eventType()
        );

        assertEquals(
                1,
                analysis.parameters().size()
        );

        assertTrue(
                analysis.dependencies().contains(
                        new DependencyInfo(
                                "inventoryStore",
                                "InventoryStore"
                        )
                )
        );

        assertTrue(
                analysis.methodCalls()
                        .stream()
                        .anyMatch(call ->
                                call.expression().equals(
                                        "inventoryStore.save(event.getItemId())"
                                )
                        )
        );
    }

    @Test
    void analyzesNotificationConsumer() throws Exception {

        ConsumerAnalysis analysis =
                analyzer.analyze(
                        Path.of(
                                "src/test/resources/consumers/NotificationConsumer.java"
                        )
                );

        assertEquals(
                "NotificationConsumer",
                analysis.className()
        );

        assertEquals(
                "UserRegistered",
                analysis.eventType()
        );

        assertEquals(
                2,
                analysis.dependencies().size()
        );

        assertEquals(
                2,
                analysis.variableInitializations().size()
        );

        assertTrue(
                analysis.variableInitializations()
                        .stream()
                        .anyMatch(initialization ->
                                initialization.variableName()
                                        .equals("user")
                        )
        );

        assertTrue(
                analysis.variableInitializations()
                        .stream()
                        .anyMatch(initialization ->
                                initialization.variableName()
                                        .equals("notification")
                        )
        );
    }

    @Test
    void analyzesDeepNestedConsumer() throws Exception {

        ConsumerAnalysis analysis =
                analyzer.analyze(
                        Path.of(
                                "src/test/resources/consumers/DeepNestedConsumer.java"
                        )
                );

        MethodCallInfo saveCall =
                analysis.methodCalls()
                        .stream()
                        .filter(call ->
                                call.methodName()
                                        .equals("save")
                        )
                        .findFirst()
                        .orElseThrow();

        assertEquals(
                1,
                saveCall.nestedCalls().size()
        );

        MethodCallInfo withCustomerCall =
                saveCall.nestedCalls().get(0);

        assertEquals(
                "withCustomer",
                withCustomerCall.methodName()
        );

        assertEquals(
                2,
                withCustomerCall.nestedCalls().size()
        );

        assertTrue(
                withCustomerCall.nestedCalls()
                        .stream()
                        .anyMatch(call ->
                                call.expression().equals(
                                        "event.getOrder()"
                                )
                        )
        );

        MethodCallInfo customerIdCall =
                withCustomerCall.nestedCalls()
                        .stream()
                        .filter(call ->
                                call.methodName()
                                        .equals("getId")
                        )
                        .findFirst()
                        .orElseThrow();

        assertEquals(
                1,
                customerIdCall.nestedCalls().size()
        );

        assertEquals(
                "event.getCustomer()",
                customerIdCall.nestedCalls()
                        .get(0)
                        .expression()
        );
    }

    @Test
    void analyzesConditionalConsumer() throws Exception {

        ConsumerAnalysis analysis =
                analyzer.analyze(
                        Path.of(
                                "src/test/resources/consumers/ConditionalConsumer.java"
                        )
                );

        assertTrue(
                analysis.methodCalls()
                        .stream()
                        .anyMatch(call ->
                                call.expression().equals(
                                        "event.isPriority()"
                                )
                        )
        );

        assertTrue(
                analysis.methodCalls()
                        .stream()
                        .anyMatch(call ->
                                call.expression().equals(
                                        "orderRepository.markPriority(event.getOrderId())"
                                )
                        )
        );

        assertTrue(
                analysis.methodCalls()
                        .stream()
                        .anyMatch(call ->
                                call.expression().equals(
                                        "notificationService.sendPriorityNotification(event.getOrderId())"
                                )
                        )
        );

        assertTrue(
                analysis.methodCalls()
                        .stream()
                        .anyMatch(call ->
                                call.expression().equals(
                                        "orderRepository.markStandard(event.getOrderId())"
                                )
                        )
        );
    }

    @Test
    void analyzesComplexConsumer() throws Exception {

        ConsumerAnalysis analysis =
                analyzer.analyze(
                        Path.of(
                                "src/test/resources/consumers/ComplexConsumer.java"
                        )
                );

        assertEquals(
                2,
                analysis.dependencies().size()
        );

        assertTrue(
                analysis.methodCalls()
                        .stream()
                        .anyMatch(call ->
                                call.expression().equals(
                                        "orderRepository.find(event.getOrderId())"
                                )
                        )
        );

        assertTrue(
                analysis.methodCalls()
                        .stream()
                        .anyMatch(call ->
                                call.expression().equals(
                                        "order.markProcessed()"
                                )
                        )
        );

        assertEquals(
                3,
                analysis.variableInitializations().size()
        );

        assertTrue(
                analysis.objectCreations().isEmpty()
        );

        assertTrue(
                analysis.variableInitializations()
                        .stream()
                        .anyMatch(initialization ->
                                initialization.variableName()
                                        .equals("summary")
                                        &&
                                        initialization
                                                .initializerObjectCreation()
                                                != null
                        )
        );
    }

    @Test
    void analyzesRealisticOrderConsumer() throws Exception {

        ConsumerAnalysis analysis =
                analyzer.analyze(
                        Path.of(
                                "src/test/resources/consumers/OrderConsumer.java"
                        )
                );

        assertEquals(
                "OrderConsumer",
                analysis.className()
        );

        assertEquals(
                "OrderCreated",
                analysis.eventType()
        );

        assertEquals(
                3,
                analysis.dependencies().size()
        );

        assertTrue(
                analysis.methodCalls()
                        .stream()
                        .anyMatch(call ->
                                call.expression().equals(
                                        "paymentClient.charge(order.getId())"
                                )
                        )
        );

        assertTrue(
                analysis.methodCalls()
                        .stream()
                        .anyMatch(call ->
                                call.expression().equals(
                                        "orderRepository.save(order)"
                                )
                        )
        );

        assertTrue(
                analysis.methodCalls()
                        .stream()
                        .anyMatch(call ->
                                call.expression()
                                        .startsWith(
                                                "eventPublisher.publish("
                                        )
                        )
        );
    }
}
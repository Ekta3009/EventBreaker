package com.eventbreaker.analyzer;

import com.eventbreaker.analyzer.model.AssignmentInfo;
import com.eventbreaker.analyzer.model.ConsumerAnalysis;
import com.eventbreaker.analyzer.model.DependencyInfo;
import com.eventbreaker.analyzer.model.MethodCallInfo;
import com.eventbreaker.analyzer.model.ObjectCreationInfo;
import com.eventbreaker.analyzer.model.VariableInitializationInfo;
import com.github.javaparser.StaticJavaParser;
import com.github.javaparser.ast.CompilationUnit;
import com.github.javaparser.ast.body.ClassOrInterfaceDeclaration;
import com.github.javaparser.ast.body.FieldDeclaration;
import com.github.javaparser.ast.body.MethodDeclaration;
import com.github.javaparser.ast.body.VariableDeclarator;
import com.github.javaparser.ast.expr.AssignExpr;
import com.github.javaparser.ast.expr.MethodCallExpr;
import com.github.javaparser.ast.expr.ObjectCreationExpr;

import java.nio.file.Path;
import java.util.ArrayList;
import java.util.List;

public class ConsumerAnalyzer {

    public ConsumerAnalysis analyze(Path sourceFile) throws Exception {

        CompilationUnit cu = StaticJavaParser.parse(sourceFile);

        List<DependencyInfo> dependencies =
                extractDependencies(cu);

        MethodDeclaration consumerMethod =
                cu.findAll(MethodDeclaration.class)
                        .stream()
                        .filter(this::isConsumerMethod)
                        .findFirst()
                        .orElseThrow(() ->
                                new IllegalArgumentException(
                                        "No @EventBreakerConsumer method found"
                                )
                        );

        String className =
                consumerMethod
                        .findAncestor(ClassOrInterfaceDeclaration.class)
                        .orElseThrow()
                        .getNameAsString();

        String eventType =
                extractEventType(consumerMethod);

        List<MethodCallInfo> methodCalls =
                extractMethodCalls(consumerMethod);

        List<ObjectCreationInfo> objectCreations =
                extractObjectCreations(consumerMethod);

        List<AssignmentInfo> assignments =
                extractAssignments(consumerMethod);

        List<VariableInitializationInfo> variableInitializations =
                extractVariableInitializations(consumerMethod);

        return new ConsumerAnalysis(
                className,
                consumerMethod.getNameAsString(),
                eventType,
                consumerMethod.getParameters()
                        .stream()
                        .map(Object::toString)
                        .toList(),
                dependencies,
                methodCalls,
                objectCreations,
                assignments,
                variableInitializations
        );
    }

    private boolean isConsumerMethod(MethodDeclaration method) {

        return method.getAnnotationByName("EventBreakerConsumer")
                .isPresent();
    }

    private String extractEventType(MethodDeclaration method) {

        return method.getAnnotationByName("EventBreakerConsumer")
                .orElseThrow()
                .asNormalAnnotationExpr()
                .getPairs()
                .stream()
                .filter(pair ->
                        pair.getNameAsString().equals("eventType")
                )
                .findFirst()
                .map(pair ->
                        pair.getValue()
                                .asStringLiteralExpr()
                                .asString()
                )
                .orElseThrow(() ->
                        new IllegalArgumentException(
                                "Consumer annotation is missing eventType"
                        )
                );
    }

    private List<DependencyInfo> extractDependencies(
            CompilationUnit cu
    ) {

        List<DependencyInfo> dependencies =
                new ArrayList<>();

        cu.findAll(FieldDeclaration.class)
                .forEach(field -> {

                    String type =
                            field.getElementType().asString();

                    field.getVariables()
                            .forEach(variable ->
                                    dependencies.add(
                                            new DependencyInfo(
                                                    variable.getNameAsString(),
                                                    type
                                            )
                                    )
                            );
                });

        return dependencies;
    }

    private List<MethodCallInfo> extractMethodCalls(
            MethodDeclaration method
    ) {

        return method.findAll(MethodCallExpr.class)
                .stream()
                .filter(this::isTopLevelMethodCall)
                .map(this::createMethodCallInfo)
                .toList();
    }

    private boolean isTopLevelMethodCall(
            MethodCallExpr call
    ) {

        /*
         * A method call nested inside another method call
         * belongs to that parent call.
         */
        if (call.findAncestor(MethodCallExpr.class)
                .isPresent()) {
            return false;
        }

        /*
         * A method call nested inside an object creation
         * belongs to that object creation.
         */
        if (call.findAncestor(ObjectCreationExpr.class)
                .isPresent()) {
            return false;
        }

        return true;
    }

    private MethodCallInfo createMethodCallInfo(
            MethodCallExpr call
    ) {

        String scope =
                call.getScope()
                        .map(Object::toString)
                        .orElse(null);

        List<String> arguments =
                call.getArguments()
                        .stream()
                        .map(Object::toString)
                        .toList();

        /*
         * Find only the direct child method calls of this
         * method call.
         *
         * Example:
         *
         * orderRepository.save(
         *     event.getOrder().withCustomer(
         *         event.getCustomer().getId()
         *     )
         * )
         *
         * Direct children of save():
         *
         *   event.getOrder().withCustomer(...)
         *
         * Direct children of withCustomer():
         *
         *   event.getCustomer().getId()
         *
         * Direct children of getId():
         *
         *   event.getCustomer()
         *
         * This preserves the actual AST hierarchy.
         */
        List<MethodCallInfo> nestedCalls =
                call.findAll(MethodCallExpr.class)
                        .stream()
                        .filter(nested -> nested != call)
                        .filter(nested ->
                                nested.findAncestor(
                                                MethodCallExpr.class
                                        )
                                        .map(parent -> parent == call)
                                        .orElse(false)
                        )
                        .map(this::createMethodCallInfo)
                        .toList();

        return new MethodCallInfo(
                call.toString(),
                scope,
                call.getNameAsString(),
                arguments,
                nestedCalls
        );
    }

    private List<ObjectCreationInfo> extractObjectCreations(
            MethodDeclaration method
    ) {

        return method.findAll(ObjectCreationExpr.class)
                .stream()
                .filter(this::isTopLevelObjectCreation)
                .map(this::createObjectCreationInfo)
                .toList();
    }

    private boolean isTopLevelObjectCreation(
            ObjectCreationExpr creation
    ) {

        /*
         * Nested object creation belongs to its parent
         * object creation.
         */
        if (creation.findAncestor(
                ObjectCreationExpr.class
        ).isPresent()) {
            return false;
        }

        /*
         * An object creation used as a variable initializer
         * is represented by VariableInitializationInfo.
         */
        if (creation.findAncestor(
                VariableDeclarator.class
        ).isPresent()) {
            return false;
        }

        return true;
    }

    private ObjectCreationInfo createObjectCreationInfo(
            ObjectCreationExpr creation
    ) {

        List<String> arguments =
                creation.getArguments()
                        .stream()
                        .map(Object::toString)
                        .toList();

        /*
         * Only collect method calls that are direct children
         * of this object creation expression.
         */
        List<MethodCallInfo> nestedCalls =
                creation.findAll(MethodCallExpr.class)
                        .stream()
                        .filter(call ->
                                call.findAncestor(
                                        MethodCallExpr.class
                                ).isEmpty()
                        )
                        .map(this::createMethodCallInfo)
                        .toList();

        return new ObjectCreationInfo(
                creation.getTypeAsString(),
                creation.toString(),
                arguments,
                nestedCalls
        );
    }

    private List<AssignmentInfo> extractAssignments(
            MethodDeclaration method
    ) {

        return method.findAll(AssignExpr.class)
                .stream()
                .map(assignment ->
                        new AssignmentInfo(
                                assignment.getTarget().toString(),
                                assignment.getValue().toString(),
                                assignment.toString()
                        )
                )
                .toList();
    }

    private List<VariableInitializationInfo>
    extractVariableInitializations(
            MethodDeclaration method
    ) {

        return method.findAll(VariableDeclarator.class)
                .stream()
                .filter(variable ->
                        variable.getInitializer().isPresent()
                )
                .map(variable -> {

                    var initializer =
                            variable.getInitializer()
                                    .orElseThrow();

                    MethodCallInfo initializerMethodCall =
                            null;

                    ObjectCreationInfo initializerObjectCreation =
                            null;

                    if (initializer instanceof MethodCallExpr methodCall) {

                        initializerMethodCall =
                                createMethodCallInfo(methodCall);
                    }

                    if (initializer instanceof ObjectCreationExpr objectCreation) {

                        initializerObjectCreation =
                                createObjectCreationInfo(
                                        objectCreation
                                );
                    }

                    return new VariableInitializationInfo(
                            variable.getNameAsString(),
                            variable.getTypeAsString(),
                            initializer.toString(),
                            initializerMethodCall,
                            initializerObjectCreation
                    );
                })
                .toList();
    }
}
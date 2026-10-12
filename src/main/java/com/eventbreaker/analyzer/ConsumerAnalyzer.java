package com.eventbreaker.analyzer;

import com.eventbreaker.analyzer.model.AssignmentInfo;
import com.eventbreaker.analyzer.model.ConsumerAnalysis;
import com.eventbreaker.analyzer.model.DependencyInfo;
import com.eventbreaker.analyzer.model.MethodCallInfo;
import com.eventbreaker.analyzer.model.ObjectCreationInfo;
import com.eventbreaker.analyzer.model.VariableInitializationInfo;
import com.github.javaparser.ParserConfiguration;
import com.github.javaparser.StaticJavaParser;
import com.github.javaparser.ast.CompilationUnit;
import com.github.javaparser.ast.body.ClassOrInterfaceDeclaration;
import com.github.javaparser.ast.body.FieldDeclaration;
import com.github.javaparser.ast.body.MethodDeclaration;
import com.github.javaparser.ast.body.Parameter;
import com.github.javaparser.ast.body.VariableDeclarator;
import com.github.javaparser.ast.expr.AnnotationExpr;
import com.github.javaparser.ast.expr.AssignExpr;
import com.github.javaparser.ast.expr.MethodCallExpr;
import com.github.javaparser.ast.expr.ObjectCreationExpr;
import com.github.javaparser.ast.type.ClassOrInterfaceType;
import com.github.javaparser.ast.type.Type;

import com.eventbreaker.analyzer.model.ListenerInfo;
import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.stream.Stream;
import java.util.ArrayList;
import java.util.Collections;
import java.util.IdentityHashMap;
import java.util.List;
import java.util.Set;

public class ConsumerAnalyzer {

    static {
        // Real code uses modern syntax (pattern matching, records, switch expressions);
        // JavaParser's default language level rejects it.
        StaticJavaParser.getParserConfiguration()
                .setLanguageLevel(ParserConfiguration.LanguageLevel.JAVA_21);
    }

    // Method-level annotations that mark an event listener (matched by simple name).
    private static final Set<String> LISTENER_ANNOTATIONS = Set.of(
            "KafkaListener",
            "RabbitListener",
            "RabbitHandler",
            "JmsListener",
            "SqsListener",
            "StreamListener",
            "EventListener",
            "TransactionalEventListener",
            "Subscribe"
    );

    // Listener parameters supplied by the messaging framework, never the event itself.
    private static final Set<String> FRAMEWORK_PARAM_TYPES = Set.of(
            "Acknowledgment",
            "Channel",
            "MessageHeaders",
            "Headers",
            "Consumer"
    );

    // Envelope types whose last type argument is the event payload.
    private static final Set<String> WRAPPER_TYPES = Set.of(
            "ConsumerRecord",
            "Message"
    );

    public ConsumerAnalysis analyze(Path sourceFile) throws Exception {
        return analyze(sourceFile, null);
    }

    /**
     * Every listener method under root (test sources skipped). Files that do not
     * parse are skipped too — a scan must not fail because of one odd file.
     */
    public List<ListenerInfo> scan(Path root) throws IOException {

        List<ListenerInfo> found = new ArrayList<>();
        List<Path> files;
        try (Stream<Path> walk = Files.walk(root)) {
            files = walk
                    .filter(p -> p.toString().endsWith(".java"))
                    .filter(p -> !p.toString().contains("/src/test/"))
                    .filter(p -> !p.toString().contains("/build/") && !p.toString().contains("/target/"))
                    .sorted()
                    .toList();
        }
        for (Path file : files) {
            CompilationUnit cu;
            try {
                cu = StaticJavaParser.parse(file);
            } catch (Exception | StackOverflowError e) {
                continue;
            }
            for (MethodDeclaration method : cu.findAll(MethodDeclaration.class)) {
                if (!isConsumerMethod(method)) {
                    continue;
                }
                String className = method.findAncestor(ClassOrInterfaceDeclaration.class)
                        .map(ClassOrInterfaceDeclaration::getNameAsString)
                        .orElse("?");
                String eventType;
                try {
                    eventType = extractEventType(method);
                } catch (RuntimeException e) {
                    continue;  // no event parameter: a lifecycle hook (e.g. onStartup), not a consumer
                }
                found.add(new ListenerInfo(file.toString(), className, method.getNameAsString(), eventType));
            }
        }
        return found;
    }

    public ConsumerAnalysis analyze(Path sourceFile, String methodName) throws Exception {

        CompilationUnit cu = StaticJavaParser.parse(sourceFile);

        List<DependencyInfo> dependencies =
                extractDependencies(cu);

        List<MethodDeclaration> listenerMethods =
                cu.findAll(MethodDeclaration.class)
                        .stream()
                        .filter(this::isConsumerMethod)
                        .toList();

        if (listenerMethods.isEmpty()) {
            throw new IllegalArgumentException(
                    "No listener method found (looked for @"
                            + String.join(", @", LISTENER_ANNOTATIONS.stream().sorted().toList())
                            + ")"
            );
        }

        MethodDeclaration consumerMethod =
                methodName == null
                        ? listenerMethods.get(0)
                        : listenerMethods.stream()
                                .filter(m -> m.getNameAsString().equals(methodName))
                                .findFirst()
                                .orElseThrow(() ->
                                        new IllegalArgumentException(
                                                "No listener method named '" + methodName
                                                        + "'. Listener methods: "
                                                        + listenerNames(listenerMethods)
                                        )
                                );

        List<String> otherListenerMethods =
                listenerMethods.stream()
                        .filter(m -> m != consumerMethod)
                        .map(MethodDeclaration::getNameAsString)
                        .distinct()
                        .toList();

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
                variableInitializations,
                otherListenerMethods
        );
    }

    private static List<String> listenerNames(List<MethodDeclaration> methods) {
        return methods.stream()
                .map(MethodDeclaration::getNameAsString)
                .distinct()
                .toList();
    }

    private boolean isConsumerMethod(MethodDeclaration method) {

        return method.getAnnotations()
                .stream()
                .anyMatch(a -> LISTENER_ANNOTATIONS.contains(simpleName(a)));
    }

    private static String simpleName(AnnotationExpr annotation) {
        return annotation.getName().getIdentifier();
    }

    private String extractEventType(MethodDeclaration method) {

        // The event is the @Payload parameter, else the first
        // parameter that is not framework-supplied (ack, channel, @Header ...).
        Parameter eventParam =
                method.getParameters()
                        .stream()
                        .filter(p -> p.getAnnotationByName("Payload").isPresent())
                        .findFirst()
                        .or(() -> method.getParameters()
                                .stream()
                                .filter(p -> !isFrameworkParam(p))
                                .findFirst())
                        .orElseThrow(() ->
                                new IllegalArgumentException(
                                        "Listener method " + method.getNameAsString()
                                                + " has no event parameter"
                                )
                        );

        return unwrapPayloadType(eventParam.getType());
    }

    private boolean isFrameworkParam(Parameter param) {

        if (param.getAnnotationByName("Header").isPresent()
                || param.getAnnotationByName("Headers").isPresent()) {
            return true;
        }
        Type type = param.getType();
        String name = type.isClassOrInterfaceType()
                ? type.asClassOrInterfaceType().getNameAsString()
                : type.asString();
        return FRAMEWORK_PARAM_TYPES.contains(name);
    }

    private String unwrapPayloadType(Type type) {

        if (type.isClassOrInterfaceType()) {
            ClassOrInterfaceType cls = type.asClassOrInterfaceType();
            if (WRAPPER_TYPES.contains(cls.getNameAsString())) {
                var args = cls.getTypeArguments().orElse(null);
                if (args != null && !args.isEmpty()) {
                    return unwrapPayloadType(args.get(args.size() - 1));
                }
            }
        }
        return type.asString();
    }

    private List<DependencyInfo> extractDependencies(
            CompilationUnit cu
    ) {

        List<DependencyInfo> dependencies =
                new ArrayList<>();

        cu.findAll(FieldDeclaration.class)
                .stream()
                // Static fields (loggers, constants, caches) are never injected dependencies.
                .filter(field -> !field.isStatic())
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

        Set<MethodDeclaration> visited =
                Collections.newSetFromMap(new IdentityHashMap<>());
        visited.add(method);
        return extractMethodCalls(method, visited);
    }

    /*
     * Top-level calls of the method, with the calls made inside same-class
     * helper methods it invokes (e.g. handle(e) -> process(e) -> repo.save())
     * inlined right after the call that reaches them. Real listeners often
     * delegate all side effects to private helpers.
     */
    private List<MethodCallInfo> extractMethodCalls(
            MethodDeclaration method,
            Set<MethodDeclaration> visited
    ) {

        List<MethodCallInfo> calls = new ArrayList<>();
        for (MethodCallExpr call : method.findAll(MethodCallExpr.class)) {
            if (isTopLevelMethodCall(call)) {
                calls.add(createMethodCallInfo(call));
            }
            for (MethodDeclaration helper : sameClassTargets(call, method)) {
                if (visited.add(helper)) {
                    calls.addAll(extractMethodCalls(helper, visited));
                }
            }
        }
        return calls;
    }

    /* Methods of the enclosing class that an unscoped / this. call may resolve to (same name and arity). */
    private List<MethodDeclaration> sameClassTargets(
            MethodCallExpr call,
            MethodDeclaration from
    ) {

        boolean ownCall = call.getScope()
                .map(scope -> scope.isThisExpr())
                .orElse(true);
        if (!ownCall) {
            return List.of();
        }
        return from.findAncestor(ClassOrInterfaceDeclaration.class)
                .map(cls -> cls.getMethodsByName(call.getNameAsString())
                        .stream()
                        .filter(m -> m.getParameters().size() == call.getArguments().size())
                        .filter(m -> m.getBody().isPresent())
                        .toList())
                .orElse(List.of());
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
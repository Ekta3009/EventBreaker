package com.eventbreaker.analyzer;

import com.eventbreaker.analyzer.model.AssignmentInfo;
import com.eventbreaker.analyzer.model.ConsumerAnalysis;
import com.eventbreaker.analyzer.model.DependencyInfo;
import com.eventbreaker.analyzer.model.MethodCallInfo;
import com.eventbreaker.analyzer.model.ObjectCreationInfo;
import com.eventbreaker.analyzer.model.VariableInitializationInfo;

import java.util.List;

public class ConsumerAnalysisReporter {

    public String format(ConsumerAnalysis analysis) {

        StringBuilder report =
                new StringBuilder();

        report.append("EventBreaker Analysis\n");
        report.append("=====================\n\n");

        report.append("Consumer:\n");
        report.append("  ")
                .append(analysis.className())
                .append(".")
                .append(analysis.methodName())
                .append("\n\n");

        report.append("Event:\n");
        report.append("  ")
                .append(analysis.eventType())
                .append("\n\n");

        report.append("Parameters:\n");

        for (String parameter :
                analysis.parameters()) {

            report.append("  ")
                    .append(parameter)
                    .append("\n");
        }

        report.append("\nDependencies:\n");

        for (DependencyInfo dependency :
                analysis.dependencies()) {

            report.append("  ")
                    .append(dependency.name())
                    .append(" → ")
                    .append(dependency.type())
                    .append("\n");
        }

        if (!analysis.methodCalls().isEmpty()) {

            report.append("\nMethod Calls:\n");

            for (MethodCallInfo call :
                    analysis.methodCalls()) {

                appendMethodCall(
                        report,
                        call,
                        "  "
                );
            }
        }

        if (!analysis.objectCreations().isEmpty()) {

            report.append("\nObject Creations:\n");

            for (ObjectCreationInfo creation :
                    analysis.objectCreations()) {

                appendObjectCreation(
                        report,
                        creation,
                        "  "
                );
            }
        }

        if (!analysis.variableInitializations().isEmpty()) {

            report.append(
                    "\nVariable Initializations:\n"
            );

            for (VariableInitializationInfo initialization :
                    analysis.variableInitializations()) {

                appendVariableInitialization(
                        report,
                        initialization
                );
            }
        }

        if (!analysis.assignments().isEmpty()) {

            report.append("\nAssignments:\n");

            for (AssignmentInfo assignment :
                    analysis.assignments()) {

                report.append("  ")
                        .append(assignment.expression())
                        .append("\n");
            }
        }

        return report.toString();
    }

    private void appendMethodCall(
            StringBuilder report,
            MethodCallInfo call,
            String indentation
    ) {

        report.append(indentation)
                .append(call.expression())
                .append("\n");

        appendNestedMethodCalls(
                report,
                call.nestedCalls(),
                indentation + "  "
        );
    }

    private void appendNestedMethodCalls(
            StringBuilder report,
            List<MethodCallInfo> calls,
            String indentation
    ) {

        for (int i = 0; i < calls.size(); i++) {

            MethodCallInfo call = calls.get(i);

            boolean last = i == calls.size() - 1;

            report.append(indentation)
                    .append(last ? "└── " : "├── ")
                    .append(call.expression())
                    .append("\n");

            appendNestedMethodCalls(
                    report,
                    call.nestedCalls(),
                    indentation + (last ? "    " : "│   ")
            );
        }
    }

    private void appendObjectCreation(
            StringBuilder report,
            ObjectCreationInfo creation,
            String indentation
    ) {

        report.append(indentation)
                .append(creation.expression())
                .append("\n");

        appendNestedMethodCalls(
                report,
                creation.nestedCalls(),
                indentation + "  "
        );
    }

    private void appendVariableInitialization(
            StringBuilder report,
            VariableInitializationInfo initialization
    ) {

        report.append("  ")
                .append(initialization.variableType())
                .append(" ")
                .append(initialization.variableName())
                .append(" = ")
                .append(initialization.initializer())
                .append("\n");

        if (initialization.initializerMethodCall() != null) {

            appendNestedMethodCalls(
                    report,
                    initialization
                            .initializerMethodCall()
                            .nestedCalls(),
                    "    "
            );
        }

        if (initialization.initializerObjectCreation() != null) {

            appendNestedMethodCalls(
                    report,
                    initialization
                            .initializerObjectCreation()
                            .nestedCalls(),
                    "    "
            );
        }
    }
}
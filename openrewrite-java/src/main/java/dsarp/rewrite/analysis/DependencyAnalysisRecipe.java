package dsarp.rewrite.analysis;

import org.openrewrite.ExecutionContext;
import org.openrewrite.Recipe;
import org.openrewrite.TreeVisitor;
import org.openrewrite.java.JavaIsoVisitor;
import org.openrewrite.java.tree.Flag;
import org.openrewrite.java.tree.J;
import org.openrewrite.java.tree.JavaType;
import org.openrewrite.java.tree.TypeUtils;

import java.util.Set;
import java.util.stream.Collectors;

public class DependencyAnalysisRecipe extends Recipe {
    private final transient MethodDependencyTable dependencies = new MethodDependencyTable(this);

    @Override public String getDisplayName() { return "Collect DSARP semantic method dependencies"; }
    @Override public String getDescription() { return "Emits resolved Java method/type dependency evidence without changing source."; }

    private static String typeName(JavaType type) {
        if (type instanceof JavaType.Array array) return typeName(array.getElemType()) + "[]";
        JavaType.FullyQualified fq = TypeUtils.asFullyQualified(type);
        return fq == null ? String.valueOf(type) : fq.getFullyQualifiedName();
    }

    @Override public TreeVisitor<?, ExecutionContext> getVisitor() {
        return new JavaIsoVisitor<>() {
            private J.MethodDeclaration current;
            private String owner;

            @Override public J.MethodDeclaration visitMethodDeclaration(J.MethodDeclaration method, ExecutionContext ctx) {
                J.MethodDeclaration previous = current;
                String previousOwner = owner;
                current = method;
                JavaType.Method mt = method.getMethodType();
                owner = mt == null || mt.getDeclaringType() == null ? null : mt.getDeclaringType().getFullyQualifiedName();
                emit(null, "method_declaration", ctx);
                if (mt != null) {
                    emit(mt.getReturnType(), "return_type", ctx);
                    for (JavaType parameter : mt.getParameterTypes()) emit(parameter, "parameter_type", ctx);
                    JavaType.FullyQualified declaring = mt.getDeclaringType();
                    if (declaring != null) {
                        emit(declaring.getSupertype(), "inheritance", ctx);
                        for (JavaType.FullyQualified iface : declaring.getInterfaces()) emit(iface, "inheritance", ctx);
                    }
                }
                J.MethodDeclaration result = super.visitMethodDeclaration(method, ctx);
                current = previous; owner = previousOwner;
                return result;
            }

            @Override public J.MethodInvocation visitMethodInvocation(J.MethodInvocation invocation, ExecutionContext ctx) {
                J.MethodInvocation result = super.visitMethodInvocation(invocation, ctx);
                JavaType.Method mt = invocation.getMethodType();
                emit(mt == null ? null : mt.getDeclaringType(), "method_call", ctx);
                return result;
            }

            @Override public J.NewClass visitNewClass(J.NewClass newClass, ExecutionContext ctx) {
                J.NewClass result = super.visitNewClass(newClass, ctx);
                emit(newClass.getType(), "constructor_call", ctx);
                return result;
            }

            @Override public J.VariableDeclarations visitVariableDeclarations(J.VariableDeclarations declarations, ExecutionContext ctx) {
                J.VariableDeclarations result = super.visitVariableDeclarations(declarations, ctx);
                emit(declarations.getType(), getCursor().firstEnclosing(J.MethodDeclaration.class) == null
                        ? "field_access" : "local_variable_type", ctx);
                return result;
            }

            @Override public J.Identifier visitIdentifier(J.Identifier identifier, ExecutionContext ctx) {
                J.Identifier result = super.visitIdentifier(identifier, ctx);
                JavaType.Variable variable = identifier.getFieldType();
                if (variable != null && variable.getOwner() instanceof JavaType.FullyQualified ownerType) {
                    emit(ownerType, "field_access", ctx);
                }
                return result;
            }

            private void emit(JavaType type, String kind, ExecutionContext ctx) {
                JavaType.FullyQualified fq = TypeUtils.asFullyQualified(type);
                if (current == null || owner == null) return;
                JavaType.Method mt = current.getMethodType();
                Set<Flag> flags = mt == null ? Set.of() : mt.getFlags();
                String signature = current.getSimpleName() + "(" + (mt == null ? "" : mt.getParameterTypes().stream()
                        .map(DependencyAnalysisRecipe::typeName).collect(Collectors.joining(","))) + ")";
                String visibility = flags.contains(Flag.Public) ? "public" : flags.contains(Flag.Protected) ? "protected"
                        : flags.contains(Flag.Private) ? "private" : "package";
                String target = fq == null ? "" : fq.getFullyQualifiedName();
                boolean sourceState = target.equals(owner);
                dependencies.insertRow(ctx, new MethodDependencyTable.Row(
                        owner, current.getSimpleName(), signature,
                        owner.contains(".") ? owner.substring(0, owner.lastIndexOf('.')) : "",
                        getCursor().firstEnclosingOrThrow(J.CompilationUnit.class).getSourcePath().toString(),
                        visibility, flags.contains(Flag.Static), flags.contains(Flag.Abstract), flags.contains(Flag.Native),
                        flags.contains(Flag.Synchronized), mt == null ? "" : typeName(mt.getReturnType()), target,
                        target.contains(".") ? target.substring(0, target.lastIndexOf('.')) : "", kind, sourceState));
            }
        };
    }
}
